"""
模組名稱：app.services.rag_admin_service
功能說明：RAG 管理交易——知識庫與文件的新增、改名、刪除、上傳（Storage bucket rag）、資訊部審核、
          文件核准／暫停、部門指派與問答選用、內容替換，以及處理一筆索引工作（rag_jobs）。
相依模組：app.db.repositories.rag_repository、app.services.rag_review_service、
          app.services.rag_selection_service、app.services.rag_indexing_service、
          app.integrations.vertex_embedding、app.integrations.document_ai、
          app.integrations.supabase_storage
注意事項：
    - 本模組控制交易（commit／rollback），repository 不 commit；規則判斷交給純邏輯 service。
    - 操作者（RagActor）由路由以已驗證身分建立：user_id、目前部門、是否資訊部；不接受表單自報。
      路由已先以 module.check 驗過查看／新增／修改／刪除權限，本模組再判斷資料範圍
      （非資訊部只能管理自己部門送審或可管理的知識庫）。
    - 檔案存在私有 bucket rag，路徑 {department_code}/RAG_{台灣時間YYYYMMDDHHMMSS}_{rag_file_id}.{副檔名}；
      先上傳物件再寫 DB，DB 失敗且資料表未指向新物件時才刪除它；刪除 DB 列成功後才刪除舊物件。
    - 處理索引工作分兩個短交易：開始（記下版本、改為索引中）→ 提交 → 讀檔、擷取、呼叫向量
      （不持有 DB 交易）→ 寫入交易中鎖定 rag_files 核對版本，相同才刪舊寫新並設 ready。
    - 索引只由資訊部在畫面手動觸發（確認上傳內容後），不做排程、不用請求結束後的背景 thread。
"""

import hashlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.module_errors import ModuleConfigurationError
from app.db.repositories import rag_repository as repository
from app.integrations.document_ai import DocumentAIClient, DocumentAIError
from app.integrations.supabase_storage import S3StorageClient, StorageError
from app.integrations.vertex_embedding import EmbeddingError, VertexEmbeddingClient
from app.schemas.rag import (
    APPROVAL_APPROVED,
    EFFECTIVE_DATE_LABELS,
    ERROR_STORAGE_READ,
    INDEX_READY,
    JOB_EXTRACT,
    JOB_PENDING,
    JOB_REINDEX,
    PARSE_PROCESSING,
    PARSE_READY,
    UPLOAD_MIME_TYPES,
    UPLOAD_SOURCE_TYPES,
    DepartmentAccess,
    DepartmentOption,
    IndexResult,
    KnowledgeBase,
    LatestJob,
    RagActor,
    RagFile,
    RagJob,
    RagRuleError,
)
from app.services import rag_indexing_service as indexing
from app.services import rag_review_service as review
from app.services import rag_selection_service as selection
from app.utils.format import TAIPEI_TZ

# 知識庫檔案的私有 bucket（rag_files.bucket_name）
RAG_BUCKET = "rag"
# 單檔上限：Vercel 函式請求本文上限約 4.5 MB，保留表單欄位空間
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
# 名稱長度上限（與畫面 maxlength 相同）
MAX_NAME_LENGTH = 160
# 一次索引的總時間預算，從 index_document 開始計算（含 DB 交易與讀檔）；
# Vercel 函式與本機 START 閘道都在 60 秒切斷，索引沿用較短的預算
INDEX_TOTAL_BUDGET_SECONDS = 25.0
# 寫入段落與向量的交易保留時間（正式 DB 在雲端，來回較慢）
INDEX_WRITE_MARGIN_SECONDS = 4.0
# 補舊檔雜湊的時間上限：ADMIN 函式 60 秒內還要完成本次上傳
LEGACY_HASH_BUDGET_SECONDS = 8.0


class RagUnavailableError(RuntimeError):
    """資料庫或 Storage 未設定、無法連線，或 RAG 資料表尚未建立；訊息可直接顯示。"""


class RagStorage(Protocol):
    """S3StorageClient 的最小介面，測試以替身注入。"""

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        """上傳物件。"""
        ...

    def get(self, key: str, *, timeout_seconds: float = ...) -> tuple[bytes, str]:
        """下載物件內容與 Content-Type。"""
        ...

    def delete(self, key: str) -> None:
        """刪除物件。"""
        ...


@dataclass(frozen=True)
class KnowledgeOverview:
    """知識庫管理畫面所需資料。"""

    knowledge_bases: tuple[KnowledgeBase, ...]
    access_rows: tuple[DepartmentAccess, ...]
    files_to_index: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentsView:
    """文件清單畫面所需資料。"""

    knowledge_base: KnowledgeBase
    access_rows: tuple[DepartmentAccess, ...]
    files: tuple[RagFile, ...]
    latest_jobs: dict[str, LatestJob]
    available_files: tuple[RagFile, ...] = ()


@dataclass(frozen=True)
class FileLibrary:
    """全部門可見的檔案與其引用、索引工作。"""

    files: tuple[RagFile, ...]
    references: dict[str, tuple[KnowledgeBase, ...]]
    latest_jobs: dict[str, LatestJob]


@dataclass(frozen=True)
class UploadItem:
    """一個上傳檔案。"""

    filename: str
    content: bytes
    title: str | None = None
    source_type: str = ""
    effective_date_status: str = ""
    effective_date: str | None = None
    source_note: str | None = None


@dataclass(frozen=True)
class UploadOutcome:
    """一個檔案的上傳結果（訊息不含檔案內容）。"""

    filename: str
    ok: bool
    message: str


def _now() -> datetime:
    return datetime.now(UTC)


def _run[T](operation: Callable[[Session], T], *, write: bool = True) -> T:
    """開啟連線執行一個交易；規則錯誤與 DB 錯誤都回滾，不留下部分寫入。"""
    try:
        db = repository.open_session()
    except (ModuleConfigurationError, ValueError, repository.SQLAlchemyError):
        raise RagUnavailableError("資料庫未設定或無法連線，請使用既有 START 啟動 ADMIN") from None
    try:
        result = operation(db)
        if write:
            db.commit()
        return result
    except (RagRuleError, RagUnavailableError):
        db.rollback()
        raise
    except repository.SQLAlchemyError:
        db.rollback()
        raise RagUnavailableError(
            "RAG 資料表讀寫失敗（可能尚未套用 Schema v1.8）；未儲存變更"
        ) from None
    finally:
        db.close()


def _require[T](value: T | None, message: str) -> T:
    if value is None:
        raise RagRuleError("NOT_FOUND", message)
    return value


def rag_storage() -> RagStorage:
    """建立 bucket rag 的 Storage 用戶端。

    Raises:
        RagUnavailableError: Storage 未設定。
    """
    try:
        return S3StorageClient.from_settings(bucket=RAG_BUCKET)
    except (StorageError, ValueError):
        raise RagUnavailableError("檔案儲存未設定，請使用既有 START 啟動 ADMIN") from None


def _clean_name(value: str, label: str) -> str:
    name = (value or "").strip()
    if not name:
        raise RagRuleError("NAME_REQUIRED", f"請輸入{label}")
    if len(name) > MAX_NAME_LENGTH:
        raise RagRuleError("NAME_TOO_LONG", f"{label}最多 {MAX_NAME_LENGTH} 字")
    return name


def new_knowledge_code(now: datetime) -> str:
    """產生知識庫代碼（小寫英數與連字號，建立後不可變更）。"""
    return f"kb-{now.astimezone(TAIPEI_TZ):%Y%m%d}-{uuid4().hex[:8]}"


def storage_key(
    department_code: str,
    rag_file_id: str,
    extension: str,
    now: datetime,
    *,
    revision: str | None = None,
) -> str:
    """物件路徑：{department_code}/RAG_{台灣時間YYYYMMDDHHMMSS}_{rag_file_id}.{副檔名}。

    替換內容時帶 revision，接在 rag_file_id 後。時間只到秒，同一份檔案同秒替換若共用物件，
    會覆寫現行原檔，失敗清除時還會把它刪掉。
    """
    name = f"{rag_file_id}_{revision}" if revision else rag_file_id
    return f"{department_code}/RAG_{now.astimezone(TAIPEI_TZ):%Y%m%d%H%M%S}_{name}.{extension}"


def upload_format(filename: str, content: bytes) -> tuple[str, str]:
    """檢查上傳檔案並回傳 (副檔名, mime_type)。

    Raises:
        RagRuleError: 格式不支援、空檔或超過大小上限。
    """
    extension = PurePosixPath(filename or "").suffix.lower().lstrip(".")
    if extension not in UPLOAD_MIME_TYPES:
        raise RagRuleError("BAD_FORMAT", "只接受 PDF、DOCX、TXT、Markdown")
    if not content:
        raise RagRuleError("EMPTY_FILE", "檔案是空的")
    if len(content) > MAX_UPLOAD_BYTES:
        raise RagRuleError("FILE_TOO_LARGE", f"檔案超過 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    return extension, UPLOAD_MIME_TYPES[extension]


def _require_manage(kb: KnowledgeBase, rows: Sequence[DepartmentAccess], actor: RagActor) -> None:
    if not selection.can_manage_knowledge_base(kb, rows, actor):
        raise RagRuleError("NOT_MANAGER", "目前部門不能管理這個知識庫")


def _load_kb_for_write(db: Session, knowledge_base_id: str, actor: RagActor) -> KnowledgeBase:
    kb = _require(repository.lock_knowledge_base(db, knowledge_base_id), "找不到指定知識庫")
    _require_manage(kb, repository.list_knowledge_base_access(db, knowledge_base_id), actor)
    return kb


def can_manage_file(rag_file: RagFile, actor: RagActor) -> bool:
    """獨立檔案只允許上傳部門或資訊部改名、替換及刪除。"""
    return actor.is_it or rag_file.owner_department_id == actor.department_id


def _load_file_for_write(db: Session, rag_file_id: str, actor: RagActor) -> RagFile:
    rag_file = _require(repository.lock_file(db, rag_file_id), "找不到指定檔案")
    if not can_manage_file(rag_file, actor):
        raise RagRuleError("NOT_FILE_OWNER", "只有上傳部門或資訊部可以修改這份共用檔案")
    return rag_file


def _delete_objects(storage: RagStorage, keys: Sequence[str]) -> None:
    """DB 已提交後清除物件；失敗只留下孤兒物件，不影響資料正確性。"""
    for key in keys:
        try:
            storage.delete(key)
        except (StorageError, ValueError):
            continue


def _discard_new_object(storage: RagStorage, rag_file_id: str, key: str) -> None:
    """DB 寫入失敗後清除新物件。提交可能已成功只是沒收到確認，資料表已指向它或查不到狀態時都保留。"""
    try:
        current = _run(lambda db: repository.get_file(db, rag_file_id), write=False)
    except RagUnavailableError:
        return
    if current is None or current.storage_path != key:
        _delete_objects(storage, [key])


# ---- 讀取 ----


def list_departments() -> list[DepartmentOption]:
    """讀取啟用中的部門。

    Raises:
        RagUnavailableError: DB 無法使用。
    """
    return _run(repository.list_departments, write=False)


def load_overview() -> KnowledgeOverview:
    """讀取全部知識庫與部門指派／選用。

    Returns:
        知識庫管理畫面所需資料。

    Raises:
        RagUnavailableError: DB 無法使用。
    """

    def operation(db: Session) -> KnowledgeOverview:
        return KnowledgeOverview(
            tuple(repository.list_knowledge_bases(db)),
            repository.list_department_access(db),
            repository.list_files_to_index(db),
        )

    return _run(operation, write=False)


def load_knowledge_base(
    knowledge_base_id: str,
) -> tuple[KnowledgeBase, tuple[DepartmentAccess, ...]]:
    """讀取一個知識庫與其部門配置。

    Raises:
        RagRuleError: 找不到知識庫。
        RagUnavailableError: DB 無法使用。
    """

    def operation(db: Session) -> tuple[KnowledgeBase, tuple[DepartmentAccess, ...]]:
        kb = _require(repository.get_knowledge_base(db, knowledge_base_id), "找不到指定知識庫")
        return kb, repository.list_knowledge_base_access(db, knowledge_base_id)

    return _run(operation, write=False)


def load_documents(knowledge_base_id: str) -> DocumentsView:
    """讀取知識庫的文件、部門配置與最近一次索引工作。

    Raises:
        RagRuleError: 找不到知識庫。
        RagUnavailableError: DB 無法使用。
    """

    def operation(db: Session) -> DocumentsView:
        kb = _require(repository.get_knowledge_base(db, knowledge_base_id), "找不到指定知識庫")
        return DocumentsView(
            kb,
            repository.list_knowledge_base_access(db, knowledge_base_id),
            tuple(repository.list_files(db, knowledge_base_id)),
            repository.list_latest_jobs(db, knowledge_base_id),
            tuple(f for f in repository.list_library_files(db) if f.is_approved),
        )

    return _run(operation, write=False)


def load_file(rag_file_id: str) -> RagFile:
    """讀取獨立檔案，全部門皆可檢視。"""
    return _run(
        lambda db: _require(repository.get_file(db, rag_file_id), "找不到指定檔案"), write=False
    )


def load_file_library() -> FileLibrary:
    """讀取全部檔案及引用，未核准檔案也保留在清單。"""

    def operation(db: Session) -> FileLibrary:
        return FileLibrary(
            tuple(repository.list_library_files(db)),
            repository.list_all_file_references(db),
            repository.list_all_latest_jobs(db),
        )

    return _run(operation, write=False)


def list_files(knowledge_base_id: str) -> list[RagFile]:
    """讀取知識庫的文件。

    Raises:
        RagUnavailableError: DB 無法使用。
    """
    return _run(lambda db: repository.list_files(db, knowledge_base_id), write=False)


def read_file_content(rag_file_id: str, storage: RagStorage | None = None) -> tuple[RagFile, bytes]:
    """讀取文件原始檔（下載或檢視文字）。

    Raises:
        RagRuleError: 找不到文件。
        RagUnavailableError: DB 或 Storage 無法使用。
    """
    rag_file = load_file(rag_file_id)
    storage = storage or rag_storage()
    try:
        content, _type = storage.get(rag_file.storage_path or "")
    except (StorageError, ValueError):
        raise RagUnavailableError("讀取原始檔案失敗，請稍後再試") from None
    return rag_file, content


# ---- 知識庫新增、改名、刪除與部門指派 ----


def create_knowledge_base(knowledge_name: str, actor: RagActor) -> KnowledgeBase:
    """新增知識庫：固定待審核（含資訊部自己建立），並指派目前部門為可讀、可管理。

    Raises:
        RagRuleError: 名稱空白、過長或重複。
        RagUnavailableError: DB 無法使用。
    """
    name = _clean_name(knowledge_name, "知識庫名稱")

    def operation(db: Session) -> KnowledgeBase:
        try:
            kb = repository.insert_knowledge_base(
                db, new_knowledge_code(_now()), name, actor.user_id, actor.department_id
            )
        except repository.IntegrityError:
            raise RagRuleError("DUPLICATE_NAME", "已有相同名稱的知識庫") from None
        repository.upsert_department_access(
            db, kb.knowledge_base_id, actor.department_id, can_manage=True
        )
        return kb

    return _run(operation)


def rename_knowledge_base(knowledge_base_id: str, knowledge_name: str, actor: RagActor) -> None:
    """修改知識庫名稱（代碼與資料夾不變）。

    Raises:
        RagRuleError: 找不到、不能管理、名稱空白或重複。
        RagUnavailableError: DB 無法使用。
    """
    name = _clean_name(knowledge_name, "知識庫名稱")

    def operation(db: Session) -> None:
        _load_kb_for_write(db, knowledge_base_id, actor)
        try:
            repository.rename_knowledge_base(db, knowledge_base_id, name)
        except repository.IntegrityError:
            raise RagRuleError("DUPLICATE_NAME", "已有相同名稱的知識庫") from None

    _run(operation)


def delete_knowledge_base(knowledge_base_id: str, actor: RagActor) -> None:
    """只刪知識庫與關聯，不建立 Storage 用戶端、不刪檔案、段落或工作。"""

    def operation(db: Session) -> None:
        _load_kb_for_write(db, knowledge_base_id, actor)
        repository.delete_knowledge_base(db, knowledge_base_id)

    _run(operation)


def add_files(knowledge_base_id: str, rag_file_ids: Sequence[str], actor: RagActor) -> None:
    """管理者加入已核准檔案；全部驗證通過後同一交易加關聯，不重建索引。"""

    def operation(db: Session) -> None:
        _load_kb_for_write(db, knowledge_base_id, actor)
        files = [
            _require(repository.lock_file(db, file_id), "找不到指定檔案")
            for file_id in sorted(set(rag_file_ids))
        ]
        if any(not f.is_approved for f in files):
            raise RagRuleError("FILE_NOT_APPROVED", "只能加入已核准的檔案")
        for rag_file in files:
            repository.insert_file_link(db, knowledge_base_id, rag_file.rag_file_id, actor.user_id)

    _run(operation)


def remove_file(
    knowledge_base_id: str, rag_file_id: str, actor: RagActor, expected_count: int
) -> None:
    """確認目前影響後移除關聯；清除空庫的部門指派由 Trigger 同交易完成。"""

    def operation(db: Session) -> None:
        kb = _load_kb_for_write(db, knowledge_base_id, actor)
        if kb.file_count != expected_count:
            raise RagRuleError("SELECTION_CHANGED", "檔案組合已變更，請重新確認移除影響")
        if rag_file_id not in {f.rag_file_id for f in repository.list_files(db, knowledge_base_id)}:
            raise RagRuleError("NOT_FOUND", "找不到指定關聯")
        repository.delete_file_link(db, knowledge_base_id, rag_file_id)

    _run(operation)


def copy_knowledge_base(knowledge_base_id: str, name: str, actor: RagActor) -> KnowledgeBase:
    """複製成新的待審查知識庫，只複製檔案關聯；建立者部門沿用既有開放規則。"""
    title = _clean_name(name, "知識庫名稱")

    def operation(db: Session) -> KnowledgeBase:
        _load_kb_for_write(db, knowledge_base_id, actor)
        files = repository.list_files(db, knowledge_base_id)
        try:
            kb = repository.insert_knowledge_base(
                db, new_knowledge_code(_now()), title, actor.user_id, actor.department_id
            )
        except repository.IntegrityError:
            raise RagRuleError("DUPLICATE_NAME", "已有相同名稱的知識庫") from None
        repository.upsert_department_access(
            db, kb.knowledge_base_id, actor.department_id, can_manage=True
        )
        for rag_file in files:
            repository.insert_file_link(
                db, kb.knowledge_base_id, rag_file.rag_file_id, actor.user_id
            )
        return _require(repository.get_knowledge_base(db, kb.knowledge_base_id), "找不到新知識庫")

    return _run(operation)


def set_department_assignment(
    knowledge_base_id: str, department_id: str, enabled: bool, actor: RagActor
) -> tuple[DepartmentAccess, ...]:
    """指派或取消指派部門（列存在＝已指派、可讀；取消時問答選用一併取消）。

    Returns:
        更新後此知識庫的部門配置。

    Raises:
        RagRuleError: 找不到、不能管理或部門不存在。
        RagUnavailableError: DB 無法使用。
    """

    def operation(db: Session) -> tuple[DepartmentAccess, ...]:
        _load_kb_for_write(db, knowledge_base_id, actor)
        if department_id not in {d.department_id for d in repository.list_departments(db)}:
            raise RagRuleError("NOT_FOUND", "找不到指定部門")
        if enabled:
            repository.upsert_department_access(db, knowledge_base_id, department_id)
        else:
            repository.delete_department_access(db, knowledge_base_id, department_id)
        return repository.list_knowledge_base_access(db, knowledge_base_id)

    return _run(operation)


# ---- 知識庫審核 ----


def approve_knowledge_base(
    knowledge_base_id: str, reviewer: UUID, is_it_reviewer: bool
) -> KnowledgeBase:
    """資訊部核准知識庫。

    Raises:
        RagRuleError: 不是資訊部、找不到或不在待審核。
        RagUnavailableError: DB 無法使用。
    """

    def operation(db: Session) -> KnowledgeBase:
        kb = _require(repository.lock_knowledge_base(db, knowledge_base_id), "找不到指定知識庫")
        approved = review.approve_knowledge_base(kb, str(reviewer), is_it_reviewer, _now())
        repository.update_knowledge_base_review(db, approved)
        return approved

    return _run(operation)


def reject_knowledge_base(
    knowledge_base_id: str, reviewer: UUID, is_it_reviewer: bool, comment: str
) -> KnowledgeBase:
    """資訊部退回知識庫，同一交易清除所有部門的問答選用。

    Raises:
        RagRuleError: 不是資訊部、找不到、已退回或原因空白。
        RagUnavailableError: DB 無法使用。
    """

    def operation(db: Session) -> KnowledgeBase:
        kb = _require(repository.lock_knowledge_base(db, knowledge_base_id), "找不到指定知識庫")
        rows = repository.lock_knowledge_base_access(db, knowledge_base_id)
        rejected, _cleared = review.reject_knowledge_base(
            kb, rows, str(reviewer), is_it_reviewer, comment, _now()
        )
        repository.update_knowledge_base_review(db, rejected)
        repository.clear_qa_for_knowledge_base(db, knowledge_base_id)
        return rejected

    return _run(operation)


def resubmit_knowledge_base(
    knowledge_base_id: str, submitted_by: UUID, submitted_department_id: UUID
) -> KnowledgeBase:
    """退回的知識庫重新送審。

    Raises:
        RagRuleError: 找不到或不在退回狀態。
        RagUnavailableError: DB 無法使用。
    """

    def operation(db: Session) -> KnowledgeBase:
        kb = _require(repository.lock_knowledge_base(db, knowledge_base_id), "找不到指定知識庫")
        again = review.resubmit_knowledge_base(kb, str(submitted_by), str(submitted_department_id))
        repository.update_knowledge_base_review(db, again)
        return again

    return _run(operation)


# ---- 文件上傳、改名與刪除 ----


def _metadata(item: UploadItem) -> tuple[date | None, str | None]:
    if item.source_type not in UPLOAD_SOURCE_TYPES:
        raise RagRuleError("SOURCE_REQUIRED", "請選擇來源類型；新上傳不可選未分類")
    if item.effective_date_status not in EFFECTIVE_DATE_LABELS:
        raise RagRuleError("DATE_STATUS_REQUIRED", "請選擇生效日狀態")
    actual: date | None = None
    if item.effective_date_status == "known":
        try:
            actual = date.fromisoformat(item.effective_date or "")
        except (ValueError, TypeError):
            raise RagRuleError("DATE_REQUIRED", "已知生效日時必須填寫有效日期") from None
    elif item.effective_date:
        raise RagRuleError("UNEXPECTED_DATE", "未知或不適用時請清空日期")
    return actual, (item.source_note or "").strip() or None


def _require_unique_content(db: Session, digest: str, exclude: str | None = None) -> None:
    repository.lock_content_hash(db, digest)
    duplicate = repository.find_duplicate_file(
        db, digest, exclude or "00000000-0000-0000-0000-000000000000"
    )
    if duplicate:
        raise RagRuleError(
            "DUPLICATE_CONTENT", f"內容完全相同的檔案「{duplicate.title}」已存在，請改用加入關聯"
        )


def _save_legacy_hash(rag_file: RagFile, digest: str) -> None:
    def operation(db: Session) -> None:
        repository.set_legacy_hash(db, rag_file.rag_file_id, rag_file.file_version, digest)

    _run(operation)


def _backfill_legacy_hashes(storage: RagStorage) -> None:
    """盡力補舊檔雜湊供查重：逐筆提交，讀不到就略過，DB 失敗或超過時間上限就停，不擋本次上傳。"""
    deadline = time.monotonic() + LEGACY_HASH_BUDGET_SECONDS
    try:
        for rag_file in _run(repository.list_unhashed_files, write=False):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                raw, _type = storage.get(rag_file.storage_path or "", timeout_seconds=remaining)
            except (StorageError, ValueError):
                continue
            _save_legacy_hash(rag_file, hashlib.sha256(raw).hexdigest())
    except RagUnavailableError:
        return


def _owner_department_code(db: Session, rag_file: RagFile) -> str:
    """替換內容的路徑跟著檔案的上傳部門，該部門已停用也一樣，不改用操作者部門。"""
    owner = rag_file.owner_department_id
    code = repository.get_department_code(db, owner) if owner else None
    if code is None:
        raise RagRuleError("NO_OWNER_DEPARTMENT", "這份檔案沒有上傳部門，無法替換，請上傳新檔案")
    return code


def _upload_one(
    knowledge_base_id: str | None,
    item: UploadItem,
    actor: RagActor,
    department_code: str,
    storage: RagStorage,
) -> UploadOutcome:
    try:
        extension, mime_type = upload_format(item.filename, item.content)
        title = _clean_name(item.title or item.filename, "檔案名稱")
        effective_date, note = _metadata(item)
        digest = hashlib.sha256(item.content).hexdigest()
        _run(lambda db: _require_unique_content(db, digest), write=False)
    except RagRuleError as error:
        return UploadOutcome(item.filename, False, error.message)
    rag_file_id = str(uuid4())
    key = storage_key(department_code, rag_file_id, extension, _now())
    try:
        storage.put(key, item.content, content_type=mime_type)
    except (StorageError, ValueError):
        return UploadOutcome(item.filename, False, "檔案儲存失敗，未建立檔案")
    rag_file = RagFile(
        rag_file_id,
        title,
        owner_department_id=actor.department_id,
        mime_type=mime_type,
        bucket_name=RAG_BUCKET,
        storage_path=key,
        original_filename=item.filename,
        size_bytes=len(item.content),
        source_type=item.source_type,
        effective_date=effective_date,
        effective_date_status=item.effective_date_status,
        source_note=note,
        content_sha256=digest,
    )

    def operation(db: Session) -> None:
        if knowledge_base_id:
            _load_kb_for_write(db, knowledge_base_id, actor)
        _require_unique_content(db, digest)
        repository.insert_file(db, rag_file, actor.user_id)
        if knowledge_base_id:
            repository.insert_file_link(db, knowledge_base_id, rag_file_id, actor.user_id)
        repository.insert_job(db, RagJob("", rag_file_id, JOB_EXTRACT, JOB_PENDING))

    try:
        _run(operation)
    except (RagRuleError, RagUnavailableError) as error:
        _discard_new_object(storage, rag_file_id, key)
        return UploadOutcome(
            item.filename,
            False,
            error.message if isinstance(error, RagRuleError) else "寫入資料庫失敗，未建立檔案",
        )
    return UploadOutcome(item.filename, True, "已上傳（待審查，等待索引與資訊部核准）")


def upload_documents(
    knowledge_base_id: str | None,
    items: Sequence[UploadItem],
    actor: RagActor,
    storage: RagStorage | None = None,
) -> list[UploadOutcome]:
    """上傳到獨立檔案庫；指定知識庫時，同一寫入交易建立檔案、關聯及索引工作。"""
    if not items:
        raise RagRuleError("FILES_REQUIRED", "請選擇要上傳的檔案")

    def check(db: Session) -> DepartmentOption:
        if knowledge_base_id:
            _load_kb_for_write(db, knowledge_base_id, actor)
        return _require(repository.get_department(db, actor.department_id), "找不到目前啟用部門")

    department = _run(check, write=False)
    storage = storage or rag_storage()
    _backfill_legacy_hashes(storage)
    return [
        _upload_one(knowledge_base_id, item, actor, department.department_code, storage)
        for item in items
    ]


def rename_file(rag_file_id: str, title: str, actor: RagActor) -> None:
    """修改文件顯示名稱（不影響核准與索引）。

    Raises:
        RagRuleError: 找不到、不能管理或名稱空白。
        RagUnavailableError: DB 無法使用。
    """
    name = _clean_name(title, "文件名稱")

    def operation(db: Session) -> None:
        _load_file_for_write(db, rag_file_id, actor)
        repository.rename_file(db, rag_file_id, name)

    _run(operation)


def delete_file(rag_file_id: str, actor: RagActor, storage: RagStorage | None = None) -> None:
    """刪除文件（僅無引用時可刪除；段落與工作由外鍵 CASCADE），提交後刪除 Storage 物件。

    Raises:
        RagRuleError: 找不到或不能管理。
        RagUnavailableError: DB 或 Storage 無法使用。
    """

    def operation(db: Session) -> tuple[RagStorage, list[str]]:
        rag_file = _load_file_for_write(db, rag_file_id, actor)
        references = repository.list_file_references(db, rag_file_id)
        if references:
            raise RagRuleError(
                "FILE_REFERENCED",
                "檔案仍被知識庫引用：" + "、".join(k.knowledge_name for k in references),
            )
        client = storage or rag_storage()
        repository.delete_file(db, rag_file_id)
        return client, [rag_file.storage_path] if rag_file.storage_path else []

    client, keys = _run(operation)
    _delete_objects(client, keys)


def replace_file_content(
    rag_file_id: str,
    filename: str,
    content: bytes,
    actor: RagActor,
    storage: RagStorage | None = None,
) -> tuple[RagFile, str]:
    """替換文件內容：上傳新物件後同一交易 file_version +1、撤銷核准、等待索引並新增 reindex 工作。

    Args:
        rag_file_id: 獨立檔案。
        filename: 新內容的檔名（改文字時沿用原檔名）。
        content: 新內容。
        actor: 操作者。
        storage: Storage 用戶端；None 時建立 bucket rag 的用戶端。

    Returns:
        (更新後的文件, 新工作 job_id)。

    Raises:
        RagRuleError: 找不到、不能管理、格式不符，或版本已被他人改變。
        RagUnavailableError: DB 或 Storage 無法使用。
    """
    extension, mime_type = upload_format(filename, content)

    def check(db: Session) -> str:
        rag_file = _load_file_for_write(db, rag_file_id, actor)
        if any(
            kb.approval_status == APPROVAL_APPROVED
            for kb in repository.list_file_references(db, rag_file_id)
        ):
            raise RagRuleError(
                "FILE_IN_APPROVED_KB", "檔案被已核准知識庫引用，請上傳新檔案後再更新關聯"
            )
        return _owner_department_code(db, rag_file)

    department_code = _run(check, write=False)
    digest = hashlib.sha256(content).hexdigest()
    storage = storage or rag_storage()
    _backfill_legacy_hashes(storage)
    _run(lambda db: _require_unique_content(db, digest, rag_file_id), write=False)
    key = storage_key(department_code, rag_file_id, extension, _now(), revision=uuid4().hex[:8])
    try:
        storage.put(key, content, content_type=mime_type)
    except (StorageError, ValueError):
        raise RagUnavailableError("檔案儲存失敗，未變更內容") from None

    def operation(db: Session) -> tuple[RagFile, str, str | None]:
        check(db)
        _require_unique_content(db, digest, rag_file_id)
        rag_file = _load_file_for_write(db, rag_file_id, actor)
        updated, job = review.revoke_file_approval(rag_file)
        updated = replace(
            updated,
            bucket_name=RAG_BUCKET,
            storage_path=key,
            original_filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            content_sha256=digest,
        )
        if repository.replace_file_content(db, updated) != 1:
            raise RagRuleError("VERSION_CHANGED", "文件已被其他人更新，請重新整理後再試")
        return updated, repository.insert_job(db, job), rag_file.storage_path

    try:
        updated, job_id, old_key = _run(operation)
    except (RagRuleError, RagUnavailableError):
        _discard_new_object(storage, rag_file_id, key)
        raise
    if old_key:
        _delete_objects(storage, [old_key])
    return updated, job_id


# ---- 文件核准與暫停 ----


def approve_file(rag_file_id: str, reviewer: UUID, is_it_reviewer: bool) -> RagFile:
    """資訊部核准文件（需已完成索引）。

    Raises:
        RagRuleError: 不是資訊部、找不到、未索引完成或已核准。
        RagUnavailableError: DB 無法使用。
    """

    def operation(db: Session) -> RagFile:
        rag_file = _require(repository.lock_file(db, rag_file_id), "找不到指定文件")
        approved = review.approve_file(rag_file, str(reviewer), is_it_reviewer, _now())
        repository.update_file_approval(db, approved)
        return approved

    return _run(operation)


def suspend_file(rag_file_id: str, is_it_reviewer: bool) -> RagFile:
    """資訊部暫停文件（取消核准）。

    Raises:
        RagRuleError: 不是資訊部、找不到或本來就未核准。
        RagUnavailableError: DB 無法使用。
    """

    def operation(db: Session) -> RagFile:
        rag_file = _require(repository.lock_file(db, rag_file_id), "找不到指定文件")
        suspended = review.suspend_file(rag_file, is_it_reviewer)
        repository.update_file_approval(db, suspended)
        return suspended

    return _run(operation)


# ---- 部門問答選用 ----


def _require_own_department(department_id: str, actor: RagActor) -> None:
    if department_id != actor.department_id and not actor.is_it:
        raise RagRuleError("OTHER_DEPARTMENT", "只能設定目前部門的部門指派")


def set_qa_sources(
    knowledge_base_ids: Sequence[str], department_id: str, actor: RagActor
) -> tuple[DepartmentAccess, ...]:
    """整組設定部門問答知識庫：全部驗證後，同一交易先清除再設定；空清單全部取消。

    權限：只能設定目前部門；資訊部可代其他部門設定。

    Raises:
        RagRuleError: 其他部門、知識庫未核准／停用、部門未指派或不可讀。
        RagUnavailableError: DB 無法使用。
    """
    _require_own_department(department_id, actor)
    selected_ids = sorted(set(knowledge_base_ids))

    def operation(db: Session) -> tuple[DepartmentAccess, ...]:
        # 各請求均按相同順序取鎖，避免多選清單順序不同造成死鎖。
        kbs = [
            _require(repository.lock_knowledge_base(db, kb_id), "找不到指定知識庫")
            for kb_id in selected_ids
        ]
        rows = repository.lock_department_access(db, department_id)
        updated = selection.set_qa_sources(kbs, rows, department_id)
        repository.clear_qa_for_department(db, department_id)
        for kb_id in selected_ids:
            if repository.set_qa(db, kb_id, department_id) != 1:
                raise RagRuleError("NO_READ_ACCESS", "此部門未指派或不可讀此知識庫")
        return updated

    return _run(operation)


# ---- 索引工作 ----


def read_storage_object(bucket_name: str, storage_path: str) -> bytes:
    """以既有 Storage 模組讀取原始檔（bucket 取自 rag_files.bucket_name）。

    Raises:
        StorageError: 讀取失敗。
    """
    return S3StorageClient.from_settings(bucket=bucket_name).get(storage_path)[0]


def _finish(job: RagJob, rag_file: RagFile, result: IndexResult) -> IndexResult:
    """寫入交易：鎖定文件核對版本後寫入段落與狀態；版本不符只記錄工作。"""

    def operation(db: Session) -> IndexResult:
        current = repository.lock_file_version(db, rag_file.rag_file_id)
        final = result
        if result.outcome == INDEX_READY:
            final = indexing.confirm_version(result, current if current is not None else -1, _now())
        if final.outcome == INDEX_READY:
            repository.replace_chunks(db, rag_file.rag_file_id, final.chunks)
        if final.parse_status is not None and current == job.file_version:
            repository.update_parse_status(
                db, rag_file.rag_file_id, rag_file.file_version, final.parse_status
            )
        repository.update_job(db, final.job)
        return final

    return _run(operation)


def process_job(
    job_id: str,
    client: VertexEmbeddingClient,
    extractor: indexing.TextExtractor | None = None,
    read_object: Callable[[str, str], bytes] = read_storage_object,
    *,
    time_budget_seconds: float = INDEX_TOTAL_BUDGET_SECONDS,
) -> IndexResult:
    """處理一筆索引工作（由畫面手動觸發）：讀檔 → 擷取文字 → 切段 → 向量 → 核對版本後寫入。

    Args:
        job_id: rag_jobs.job_id。
        client: 向量用戶端（呼叫端以 VertexEmbeddingClient.from_settings() 建立並負責關閉）。
        extractor: 文字擷取；預設只支援 txt／md，其他格式標為失敗。
        read_object: 讀取 Storage 物件（bucket, path）→ bytes。
        time_budget_seconds: 擷取與向量共用的總時間預算。

    Returns:
        索引結果：ready、stale（版本不符已丟棄）或 failed（錯誤代碼在 error_code）。

    Raises:
        RagRuleError: 找不到工作、他人處理中或不在等待中。
        RagUnavailableError: DB 無法使用。
    """
    extractor = extractor or indexing.PlainTextExtractor()
    deadline = time.monotonic() + time_budget_seconds

    def begin(db: Session) -> tuple[RagJob, RagFile]:
        # 固定先鎖檔案再鎖工作，與寫回結果、刪除檔案（連帶刪工作）同順序，才不會互相等待。
        queued = _require(repository.get_job(db, job_id), "找不到等待中的索引工作，或他人正在處理")
        rag_file = _require(repository.lock_file(db, queued.rag_file_id), "找不到工作對應的文件")
        job = _require(repository.lock_job(db, job_id), "找不到等待中的索引工作，或他人正在處理")
        running, processing = indexing.start_job(job, rag_file, _now())
        repository.update_job(db, running)
        repository.update_parse_status(
            db, rag_file.rag_file_id, rag_file.file_version, processing.parse_status
        )
        return running, rag_file

    job, rag_file = _run(begin)
    try:
        content = read_object(rag_file.bucket_name or "", rag_file.storage_path or "")
    except (StorageError, ValueError):
        return _finish(job, rag_file, indexing.failed_result(job, ERROR_STORAGE_READ, _now()))
    try:
        extracted = extractor.extract(
            content,
            filename=rag_file.original_filename or rag_file.storage_path or "",
            mime_type=rag_file.mime_type,
        )
    except indexing.TextExtractionError as error:
        return _finish(job, rag_file, indexing.failed_result(job, error.code, _now()))
    remaining = deadline - time.monotonic() - INDEX_WRITE_MARGIN_SECONDS
    if remaining <= 0:
        return _finish(job, rag_file, indexing.failed_result(job, "embedding_timeout", _now()))
    result = indexing.embed_chunks(
        job, rag_file, extracted.text, client, _now, time_budget_seconds=remaining
    )
    return _finish(job, rag_file, result)


def queue_reindex(knowledge_base_id: str, actor: RagActor) -> int:
    """資訊部把知識庫內已索引的文件重新排入索引（切段規則調整後使用），之後再逐份建立索引。

    排入後文件在重建完成前不會被檢索（parse_status 不是 ready）。

    Returns:
        排入的文件數。

    Raises:
        RagRuleError: 不是資訊部或找不到知識庫。
        RagUnavailableError: DB 無法使用。
    """
    if not actor.is_it:
        raise RagRuleError("NOT_IT_REVIEWER", "只有資訊部可以重新建立索引")

    def operation(db: Session) -> int:
        _require(repository.lock_knowledge_base(db, knowledge_base_id), "找不到指定知識庫")
        return repository.queue_reindex(db, knowledge_base_id)

    return _run(operation)


def index_document(
    rag_file_id: str,
    actor: RagActor,
    *,
    embedding_factory: Callable[[], VertexEmbeddingClient] = VertexEmbeddingClient.from_settings,
    pdf_client_factory: Callable[[], indexing.PdfProcessor] = DocumentAIClient.from_settings,
    read_object: Callable[[str, str], bytes] = read_storage_object,
) -> IndexResult:
    """資訊部手動索引一份文件：取最早的等待中工作；沒有且文件失敗或未索引時新增 reindex 工作，再處理。

    索引（文字擷取、向量）只由資訊部在確認上傳內容後觸發，退件的文件不會產生費用。

    Raises:
        RagRuleError: 不是資訊部、找不到、已索引完成或處理中。
        RagUnavailableError: DB 無法使用，或向量服務未設定。
    """
    if not actor.is_it:
        raise RagRuleError("NOT_IT_REVIEWER", "只有資訊部可以建立索引")
    deadline = time.monotonic() + INDEX_TOTAL_BUDGET_SECONDS

    def prepare(db: Session) -> str:
        rag_file = _load_file_for_write(db, rag_file_id, actor)
        job_id = repository.find_pending_job(db, rag_file_id)
        if job_id is not None:
            return job_id
        if rag_file.parse_status == PARSE_PROCESSING:
            raise RagRuleError("PROCESSING", "此文件正在索引中，請稍後重新整理")
        if rag_file.parse_status == PARSE_READY:
            raise RagRuleError("ALREADY_INDEXED", "此文件已完成索引")
        return repository.insert_job(db, RagJob("", rag_file_id, JOB_REINDEX, JOB_PENDING))

    job_id = _run(prepare)
    extractor = indexing.DocumentTextExtractor(pdf_client_factory)
    try:
        with embedding_factory() as client:
            return process_job(
                job_id,
                client,
                extractor,
                read_object,
                time_budget_seconds=deadline - time.monotonic(),
            )
    except (EmbeddingError, DocumentAIError):
        raise RagUnavailableError("向量或文字辨識服務未設定，請回報專案負責人") from None
    finally:
        extractor.close()
