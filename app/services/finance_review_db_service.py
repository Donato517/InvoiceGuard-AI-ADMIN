"""模組名稱：app.services.finance_review_db_service
功能說明：財務審核與資料庫的銜接——讀取待審案件（含欄位確認、修改軌跡、審核歷程），以及把核准、退回、課稅別更正
    在同一個交易寫入 public.invoices、invoice_status_history 與 invoice_field_reviews。
相依模組：app.db.repositories.finance_review_repository、app.services.finance_review_service
注意事項：
    - 核准條件、狀態轉換與退回項目的規則仍在 finance_review_service（純函式）；這裡只負責讀取與交易。
    - 交易在這裡控制（成功 commit、失敗 rollback）；寫入條件為「資料庫狀態仍等於 expected_status」，
      已被別人處理時丟 ReviewConflictError，不寫任何資料。
    - 財務填的會計科目與扣抵代號，目前 invoices 沒有對應欄位（Schema 由 API 管理），核准時以文字寫進
      invoice_status_history.reason 留存；待 Schema 補欄位後改寫欄位。
    - 本機 ADMIN 連的是正式 DB，寫入即正式資料寫入；單元測試以替身 Session 驗證，不連線。
"""

import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.db.repositories import case_repository
from app.db.repositories import finance_review_repository as repo
from app.schemas.finance_review import ReviewCase, StatusChange
from app.services.finance_review_service import build_review_case

SessionFactory = Callable[[], Session]


class ReviewConflictError(Exception):
    """案件狀態已被別人改變（或案件不存在），這次操作沒有寫入任何資料。"""


class ReviewUnavailableError(Exception):
    """資料庫未設定、無法連線或寫入失敗；沒有寫入任何資料。

    Attributes:
        reason: 失敗分類（只由 SQLSTATE 判斷，不含任何資料）：schema_missing 資料表或欄位不存在
            （通常是 API 的 Migration 尚未套用）、permission_denied 權限或 RLS 拒絕、unavailable 其他。
        trace_id: 與伺服器日誌對照用的短碼；沒有日誌的情況為空字串。
    """

    def __init__(self, reason: str = "unavailable", trace_id: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.trace_id = trace_id


logger = logging.getLogger(__name__)
# SQLSTATE → 分類：42P01 資料表不存在、42703 欄位不存在、42501 權限不足（含 RLS 拒絕寫入）
_REASONS = {"42P01": "schema_missing", "42703": "schema_missing", "42501": "permission_denied"}


def _unavailable(stage: str, exc: Exception, invoice_id: str) -> ReviewUnavailableError:
    """記錄資料庫錯誤的類型與位置後，回傳給畫面用的通用錯誤；日誌不含 SQL 參數、連線字串或發票內容。

    只記錄：追蹤碼、操作階段、例外類別、SQLSTATE、資料表與約束名稱（結構名稱）、案件 UUID。
    """
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    diag = getattr(orig, "diag", None)
    trace_id = uuid4().hex[:8]
    logger.error(
        "finance_review_db_failure trace=%s stage=%s error=%s sqlstate=%s table=%s constraint=%s invoice=%s",
        trace_id,
        getattr(exc, "ig_stage", None) or stage,
        type(orig).__name__ if orig is not None else type(exc).__name__,
        sqlstate,
        getattr(diag, "table_name", None),
        getattr(diag, "constraint_name", None),
        invoice_id,
    )
    return ReviewUnavailableError(_REASONS.get(str(sqlstate), "unavailable"), trace_id)


@dataclass(frozen=True)
class LoadedReview:
    """從資料庫讀到的審核資料。

    Attributes:
        case: 審核用案件（輪次已篩選）。
        changes: invoice_changes 原始列，已依時間排序，供 build_change_history。
        history: 審核歷程（StatusChange，供畫面顯示）。
        department_id: 案件所屬部門（申請人端依部門範圍判斷可否操作）。
    """

    case: ReviewCase
    changes: list[dict[str, object]]
    history: list[StatusChange]
    department_id: str | None = None
    carryovers: list[dict[str, object]] = field(default_factory=list)
    return_items: list[dict[str, object]] = field(default_factory=list)
    snapshots: list[dict[str, object]] = field(default_factory=list)


def is_uuid(value: object) -> bool:
    """是否為合法 UUID 字串（資料庫發票的 ID 都是 UUID；示範發票是整數）。"""
    try:
        UUID(str(value))
    except ValueError:
        return False
    return True


def load(
    invoice_id: str, *, session_factory: SessionFactory = case_repository.open_session
) -> LoadedReview | None:
    """讀取一張案件的審核資料；不存在或仍是草稿回 None。

    Raises:
        ReviewUnavailableError: 資料庫無法使用。
    """
    try:
        db = session_factory()
    except Exception:
        raise ReviewUnavailableError from None
    try:
        case_repository.begin_read_snapshot(db)
        row = repo.get_review_row(db, invoice_id)
        if row is None:
            return None
        reviews = repo.list_field_reviews(db, invoice_id)
        changes = repo.list_changes(db, invoice_id)
        history_rows = repo.list_history(db, invoice_id)
        carryovers = repo.list_carryovers(db, invoice_id)
        return_items = repo.list_return_items(db, invoice_id)
        snapshots = repo.list_snapshots(db, invoice_id)
    except repo.SQLAlchemyError as exc:
        raise _unavailable("load", exc, invoice_id) from None
    finally:
        _close(db)
    return LoadedReview(
        case=build_review_case(row, reviews),
        department_id=row.get("department_id"),
        carryovers=carryovers,  # type: ignore[arg-type]
        return_items=return_items,  # type: ignore[arg-type]
        snapshots=snapshots,  # type: ignore[arg-type]
        changes=changes,  # type: ignore[arg-type]
        history=[
            StatusChange(
                invoice_id=invoice_id,
                old_status=str(item["old_status"] or ""),
                new_status=str(item["new_status"]),
                reason=item["reason"],
                changed_by=str(item["changed_by"] or ""),
                changed_at=item["changed_at"],
            )
            for item in history_rows
        ],
    )


def commit_change(
    change: StatusChange,
    *,
    case: ReviewCase,
    tax_type: str | None,
    record_reviewer: bool = True,
    session_factory: SessionFactory = case_repository.open_session,
) -> None:
    """把核准、退回或申請人終止寫入資料庫（狀態、審核歷程、退回項目在同一個交易）。

    Args:
        change: finance_review_service 產生的狀態異動；changed_by 必須是 UUID。
        case: 異動前的審核資料（取輪次與課稅別）。
        tax_type: 要一併寫入的課稅別；None 保持不變。
        record_reviewer: 是否記為審核者；申請人終止傳 False。

    Raises:
        ReviewConflictError: 資料庫狀態已不是 change.expected_status。
        ReviewUnavailableError: 資料庫無法使用或寫入失敗。
    """
    if not is_uuid(change.changed_by) or not is_uuid(change.invoice_id):
        raise ReviewUnavailableError
    try:
        db = session_factory()
    except Exception:
        raise ReviewUnavailableError from None
    try:
        applied = repo.apply_status_change(
            db,
            change,
            tax_type=tax_type,
            submission_round=case.submission_round,
            record_reviewer=record_reviewer,
        )
        if not applied:
            db.rollback()
            raise ReviewConflictError
        db.commit()
    except ReviewConflictError:
        raise
    except repo.SQLAlchemyError as exc:
        db.rollback()
        raise _unavailable("commit_change", exc, change.invoice_id) from None
    finally:
        _close(db)


def save_tax_type(
    invoice_id: str,
    tax_type: str,
    expected_status: str,
    *,
    session_factory: SessionFactory = case_repository.open_session,
) -> None:
    """財務更正課稅別並寫入資料庫。

    Raises:
        ReviewConflictError: 案件狀態已不是 expected_status。
        ReviewUnavailableError: 資料庫無法使用或寫入失敗。
    """
    if not is_uuid(invoice_id):
        raise ReviewUnavailableError
    try:
        db = session_factory()
    except Exception:
        raise ReviewUnavailableError from None
    try:
        if not repo.update_tax_type(db, invoice_id, tax_type, expected_status):
            db.rollback()
            raise ReviewConflictError
        db.commit()
    except ReviewConflictError:
        raise
    except repo.SQLAlchemyError as exc:
        db.rollback()
        raise _unavailable("save_tax_type", exc, invoice_id) from None
    finally:
        _close(db)


def accounting_note(
    *, deduction: str | None, expense_category: str | None, account: str | None
) -> str | None:
    """核准時留存的會計填寫內容（無對應欄位，寫在歷程原因）；三項都沒有時回 None。"""
    parts = [
        f"扣抵代號：{deduction}" if deduction else None,
        f"費用大項：{expense_category}" if expense_category else None,
        f"會計科目：{account}" if account else None,
    ]
    text = "；".join(part for part in parts if part)
    return text or None


def _close(db: Session) -> None:
    with suppress(Exception):  # 關閉失敗不覆蓋原本的結果或錯誤
        db.close()
