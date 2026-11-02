"""模組名稱：app.services.case_service
功能說明：ADMIN 唯讀讀取前台已提交的真實發票案件——列表、詳情，以及從私有 Storage 安全讀取原圖。
相依模組：app.db.repositories.case_repository、app.integrations.supabase_storage
注意事項：
    - 全部唯讀（READ ONLY 交易，rollback 結束），沒有任何建立、修改、核准或退回。
    - 案件層級權限（哪個部門能看哪些案件）尚未由 E／F 核定：預設政策 DenyAllCasePolicy 一律拒絕。
      政策由 router 的相依注入提供，測試以隔離身分覆寫；核定後只需替換政策，不用改查詢。
    - 原圖：物件路徑只取自 invoice_files（is_current）、不接受呼叫端傳入的路徑；bucket 必須是 invoices；
      先驗案件範圍再讀 Storage；Storage 失敗回明確錯誤，不回假圖。
    - validation_results 是「送出當下」保存的檢核結果，畫面須標示，不代表目前資料的重新檢核。
"""

from collections.abc import Callable, Sequence
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.repositories import case_repository as repo
from app.integrations.supabase_storage import S3StorageClient, StorageError
from app.schemas.case import (
    IMAGE_MIME_TYPES,
    LISTED_STATUSES,
    CaseDataInvalidError,
    CaseDetail,
    CaseFileMissingError,
    CaseNoImageError,
    CaseNotFoundError,
    CasePage,
    CaseScope,
    CaseStorageError,
    CaseStorageUnavailableError,
    CaseUnavailableError,
    CaseWarning,
)
from app.schemas.e_access import EffectiveDepartment
from app.schemas.e_admin import DeniedError, Identity

INVOICE_BUCKET = "invoices"
# 稍後重試可能恢復的 Storage 錯誤；其餘（拒絕、未設定、類型被拒）需管理員處理
TRANSIENT_STORAGE_CODES = frozenset({"storage_unreachable", "storage_failed"})
MAX_PAGE_SIZE = 100


class StorageReader(Protocol):
    """S3StorageClient 的最小介面，測試以替身注入。"""

    def get(self, key: str, *, timeout_seconds: float = ...) -> tuple[bytes, str]: ...


SessionFactory = Callable[[], Session]
StorageFactory = Callable[[str], StorageReader]


class CasePolicy(Protocol):
    """決定目前身分可讀哪些案件；規則待 E／F 核定。"""

    def scope(
        self, identity: Identity, departments: Sequence[EffectiveDepartment]
    ) -> CaseScope: ...


class DenyAllCasePolicy:
    """安全預設：案件層級規則核定前，任何人都不能讀取案件。"""

    def scope(self, identity: Identity, departments: Sequence[EffectiveDepartment]) -> CaseScope:
        return CaseScope()


def get_case_policy() -> CasePolicy:
    """FastAPI 相依：目前的案件範圍政策。核定新規則時只改這裡（測試以 dependency_overrides 覆寫）。"""
    return DenyAllCasePolicy()


def default_storage(bucket: str) -> StorageReader:
    """以 S3_* 設定建立指定 bucket 的用戶端；未設定回 Storage 錯誤。"""
    try:
        return S3StorageClient.from_settings(bucket=bucket)
    except (StorageError, ValueError):
        raise CaseStorageError from None


def _require_scope(scope: CaseScope) -> None:
    if scope.kind not in ("department", "all"):
        raise DeniedError


def list_cases(
    scope: CaseScope,
    *,
    page: int,
    page_size: int,
    status: str | None,
    session_factory: SessionFactory = repo.open_session,
) -> CasePage:
    """案件列表：只含範圍內、非草稿案件；status 只接受已列出的簽核狀態，其他視為找不到資料。"""
    _require_scope(scope)
    if status is not None and status not in LISTED_STATUSES:
        raise CaseNotFoundError
    page = max(page, 1)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE)
    department_id = scope.department_id if scope.kind == "department" else None
    db = session_factory()
    try:
        repo.begin_read_snapshot(db)
        total = repo.count_cases(db, department_id=department_id, status=status)
        rows = repo.list_cases(
            db,
            department_id=department_id,
            status=status,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
    except repo.SQLAlchemyError:
        raise CaseUnavailableError from None
    finally:
        _finish(db)
    return CasePage(items=tuple(rows), total=total, page=page, page_size=page_size)


def get_case_detail(
    scope: CaseScope, invoice_id: UUID, *, session_factory: SessionFactory = repo.open_session
) -> CaseDetail:
    """案件詳情；不存在、草稿與範圍外一律 CaseNotFoundError。"""
    _require_scope(scope)
    db = session_factory()
    try:
        repo.begin_read_snapshot(db)
        header = repo.get_case(db, invoice_id)
        if header is None or not scope.allows(header.department_id):
            raise CaseNotFoundError
        file = repo.get_current_file(db, invoice_id)
        return CaseDetail(
            header=header,
            items=tuple(repo.list_items(db, invoice_id)),
            file=None if file is None else file[0],
            field_reviews=tuple(repo.list_field_reviews(db, invoice_id)),
            changes=tuple(repo.list_changes(db, invoice_id)),
            status_history=tuple(repo.list_status_history(db, invoice_id)),
            recognition=repo.get_latest_recognition(db, invoice_id),
            warnings=parse_warnings(header.validation_results),
        )
    except repo.SQLAlchemyError:
        raise CaseUnavailableError from None
    finally:
        _finish(db)


def get_case_image(
    scope: CaseScope,
    invoice_id: UUID,
    *,
    session_factory: SessionFactory = repo.open_session,
    storage_factory: StorageFactory = default_storage,
) -> tuple[bytes, str]:
    """讀取案件目前有效的原始影像，回傳 (內容, MIME)。

    Raises:
        DeniedError: 範圍政策拒絕。
        CaseNotFoundError: 案件不存在或不在範圍內。
        CaseNoImageError: 沒有目前影像紀錄，或類型不被允許。
        CaseFileMissingError: Storage 沒有該物件。
        CaseStorageError: Storage 拒絕或未設定（需管理員處理）。
        CaseStorageUnavailableError: Storage 暫時連不上或暫時失敗。
        CaseUnavailableError: DB 讀取失敗。
    """
    _require_scope(scope)
    db = session_factory()
    try:
        repo.begin_read_snapshot(db)
        header = repo.get_case(db, invoice_id)
        if header is None or not scope.allows(header.department_id):
            raise CaseNotFoundError
        file = repo.get_current_file(db, invoice_id)
    except repo.SQLAlchemyError:
        raise CaseUnavailableError from None
    finally:
        _finish(db)
    if file is None:
        raise CaseNoImageError
    location = file[1]
    if location.bucket_name != INVOICE_BUCKET or location.mime_type not in IMAGE_MIME_TYPES:
        raise CaseNoImageError
    try:
        content, _stored_type = storage_factory(INVOICE_BUCKET).get(location.storage_path)
    except StorageError as error:
        if error.code == "storage_not_found":
            raise CaseFileMissingError from None
        if error.code in TRANSIENT_STORAGE_CODES:
            raise CaseStorageUnavailableError from None
        raise CaseStorageError from None
    return content, location.mime_type


def parse_warnings(validation_results: Any) -> tuple[CaseWarning, ...]:
    """把送出當下保存的 validation_results 轉成警示；結構不符就拒絕（不略過壞資料）。"""
    if validation_results is None or validation_results == {}:
        return ()
    if not isinstance(validation_results, dict):
        raise CaseDataInvalidError
    warnings = validation_results.get("warnings", [])
    if not isinstance(warnings, list):
        raise CaseDataInvalidError
    parsed: list[CaseWarning] = []
    for entry in warnings:
        if not isinstance(entry, dict):
            raise CaseDataInvalidError
        values = [entry.get(key) for key in ("field", "code", "message")]
        if not all(isinstance(value, str) for value in values):
            raise CaseDataInvalidError
        parsed.append(CaseWarning(*values))
    return tuple(parsed)


def _finish(db: Session) -> None:
    """唯讀交易一律 rollback 後關閉；不 commit。"""
    try:
        db.rollback()
    finally:
        db.close()
