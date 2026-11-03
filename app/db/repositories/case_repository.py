"""模組名稱：app.db.repositories.case_repository
功能說明：以固定 SQL 與綁定參數「唯讀」讀取前台已提交的真實發票案件（Schema v1.8 的 invoices、
          invoice_items、invoice_files、invoice_changes、invoice_status_history、invoice_field_reviews、
          ai_recognition_logs），供 ADMIN 案件列表、詳情與原圖讀取。
注意事項：
    - 只有 SELECT；begin_read_snapshot 以 READ ONLY 交易開始，即使程式出錯也不可能寫入。
    - 不新增 ORM Model 或 migration；表結構由 API 專案管理，欄位名稱與 Schema v1.8 一致。
    - repository 不 commit；UUID 參數在 SQL 中 CAST。草稿（status='draft'）不列出也不可讀。
    - 檔案位置（bucket_name、storage_path）只由 get_current_file_location 回傳給伺服器端。
"""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError as SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_engine
from app.schemas.case import (
    CaseChange,
    CaseFieldReview,
    CaseFileInfo,
    CaseFileLocation,
    CaseHeader,
    CaseItem,
    CaseRecognition,
    CaseStatusEntry,
    CaseSummary,
    CaseUnavailableError,
)

# 範圍條件：department_id 為 NULL 代表不限部門（scope.kind=all）。
_SCOPE = "(CAST(:department_id AS uuid) IS NULL OR i.department_id = CAST(:department_id AS uuid))"
_STATUS = "(CAST(:status AS text) IS NULL OR i.status = CAST(:status AS text))"

_SUMMARY_COLUMNS = """i.invoice_id, i.invoice_track, i.invoice_number, i.invoice_date, i.seller_name,
    i.total_amount, i.status, i.submission_round, i.submitted_by, p.display_name AS submitter_name,
    i.department_id, d.department_name, i.submitted_at"""
_FROM = """FROM public.invoices i
    LEFT JOIN public.user_profiles p ON p.user_id = i.submitted_by
    LEFT JOIN public.departments d ON d.department_id = i.department_id"""

BEGIN_READ_ONLY = text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")

COUNT_CASES = text(f"""
    SELECT count(*) AS total {_FROM}
    WHERE i.status <> 'draft' AND {_SCOPE} AND {_STATUS}
""")
LIST_CASES = text(f"""
    SELECT {_SUMMARY_COLUMNS} {_FROM}
    WHERE i.status <> 'draft' AND {_SCOPE} AND {_STATUS}
    ORDER BY i.submitted_at DESC NULLS LAST, i.created_at DESC, i.invoice_id DESC
    LIMIT :limit OFFSET :offset
""")
GET_CASE = text(f"""
    SELECT {_SUMMARY_COLUMNS}, i.tax_period, i.seller_tax_id, i.buyer_tax_id, i.buyer_name,
        i.sales_amount, i.tax_amount, i.total_amount_zh, i.tax_type, i.invoice_kind,
        i.validation_results, i.reviewed_by, i.reviewed_at, i.created_at, i.updated_at
    {_FROM}
    WHERE i.invoice_id = CAST(:invoice_id AS uuid) AND i.status <> 'draft'
""")
LIST_ITEMS = text("""
    SELECT item_id, line_no, item_name, quantity, unit_price, line_amount
    FROM public.invoice_items WHERE invoice_id = CAST(:invoice_id AS uuid) ORDER BY line_no
""")
GET_CURRENT_FILE = text("""
    SELECT file_id, bucket_name, storage_path, original_filename, mime_type, size_bytes, version_no,
        uploaded_at
    FROM public.invoice_files WHERE invoice_id = CAST(:invoice_id AS uuid) AND is_current = true
""")
LIST_FIELD_REVIEWS = text("""
    SELECT r.submission_round, r.field_scope, it.line_no, r.field_name, r.ai_original_value,
        r.submitted_value, r.is_checked, r.checked_at, r.review_comment
    FROM public.invoice_field_reviews r
    LEFT JOIN public.invoice_items it ON it.item_id = r.item_id AND it.invoice_id = r.invoice_id
    WHERE r.invoice_id = CAST(:invoice_id AS uuid)
    ORDER BY r.submission_round, r.field_scope, it.line_no NULLS FIRST, r.field_name
""")
LIST_CHANGES = text("""
    SELECT c.change_id, c.submission_round, it.line_no, c.field_name, c.old_value, c.new_value,
        c.change_source, c.changed_by, p.display_name AS changer_name, c.changed_at
    FROM public.invoice_changes c
    LEFT JOIN public.invoice_items it ON it.item_id = c.item_id AND it.invoice_id = c.invoice_id
    LEFT JOIN public.user_profiles p ON p.user_id = c.changed_by
    WHERE c.invoice_id = CAST(:invoice_id AS uuid)
    ORDER BY c.changed_at, c.change_id
""")
LIST_STATUS_HISTORY = text("""
    SELECT h.history_id, h.old_status, h.new_status, h.reason, h.changed_by,
        p.display_name AS changer_name, h.changed_at
    FROM public.invoice_status_history h
    LEFT JOIN public.user_profiles p ON p.user_id = h.changed_by
    WHERE h.invoice_id = CAST(:invoice_id AS uuid)
    ORDER BY h.changed_at, h.history_id
""")
GET_LATEST_RECOGNITION = text("""
    SELECT recognition_id, ai_provider, model_name, result_status, confidence_score,
        extracted_fields, created_at, completed_at
    FROM public.ai_recognition_logs WHERE invoice_id = CAST(:invoice_id AS uuid)
    ORDER BY created_at DESC, recognition_id DESC LIMIT 1
""")


def open_session() -> Session:
    """建立唯讀用 Session；DB 未設定或無法建立時回 503 類錯誤。"""
    try:
        return SessionLocal(bind=get_engine())
    except Exception:
        raise CaseUnavailableError from None


FIND_INVOICE_FOR_RECOGNITION = text("""
    SELECT invoice_id FROM public.ai_recognition_logs
    WHERE recognition_id = CAST(:recognition_id AS uuid)
      AND requested_by = CAST(:user_id AS uuid)
      AND invoice_id IS NOT NULL
""")


def find_invoice_id_for_recognition(
    db: Session, recognition_id: UUID, user_id: UUID
) -> UUID | None:
    """本人這次辨識是否已送出成案件（提交成功後 API 會回填 invoice_id）；沒有則 None。

    用於提交逾時／中斷後確認結果：只查本人的辨識紀錄，不洩漏他人案件。
    """
    row = db.execute(
        FIND_INVOICE_FOR_RECOGNITION,
        {"recognition_id": str(recognition_id), "user_id": str(user_id)},
    ).scalar_one_or_none()
    return None if row is None else UUID(str(row))


def begin_read_snapshot(db: Session) -> None:
    """以 READ ONLY 快照開始讀取；必須是交易的第一個陳述式，之後所有查詢看到同一份資料。"""
    db.execute(BEGIN_READ_ONLY)


def count_cases(db: Session, *, department_id: UUID | None, status: str | None) -> int:
    """符合條件的案件總數（不含草稿）；department_id=None 為不限部門。"""
    params = {"department_id": _text(department_id), "status": status}
    return int(db.execute(COUNT_CASES, params).scalar_one())


def list_cases(
    db: Session, *, department_id: UUID | None, status: str | None, limit: int, offset: int
) -> list[CaseSummary]:
    """案件一頁（最新送出在前，順序固定）。"""
    params = {
        "department_id": _text(department_id),
        "status": status,
        "limit": limit,
        "offset": offset,
    }
    return [CaseSummary(**row) for row in db.execute(LIST_CASES, params).mappings()]


def get_case(db: Session, invoice_id: UUID) -> CaseHeader | None:
    """案件表頭；不存在或為草稿時為 None。"""
    row = db.execute(GET_CASE, {"invoice_id": str(invoice_id)}).mappings().one_or_none()
    return None if row is None else CaseHeader(**row)


def list_items(db: Session, invoice_id: UUID) -> list[CaseItem]:
    return [CaseItem(**row) for row in db.execute(LIST_ITEMS, _id(invoice_id)).mappings()]


def get_current_file(db: Session, invoice_id: UUID) -> tuple[CaseFileInfo, CaseFileLocation] | None:
    """目前有效影像（is_current）的可顯示中繼資料與伺服器端物件位置；沒有則 None。"""
    row = db.execute(GET_CURRENT_FILE, _id(invoice_id)).mappings().one_or_none()
    if row is None:
        return None
    info = CaseFileInfo(
        file_id=row["file_id"],
        original_filename=row["original_filename"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        version_no=row["version_no"],
        uploaded_at=row["uploaded_at"],
    )
    location = CaseFileLocation(
        file_id=row["file_id"],
        bucket_name=row["bucket_name"],
        storage_path=row["storage_path"],
        mime_type=row["mime_type"],
    )
    return info, location


def list_field_reviews(db: Session, invoice_id: UUID) -> list[CaseFieldReview]:
    rows = db.execute(LIST_FIELD_REVIEWS, _id(invoice_id)).mappings()
    return [
        CaseFieldReview(
            submission_round=row["submission_round"],
            field_scope=row["field_scope"],
            line_no=row["line_no"],
            field_name=row["field_name"],
            ai_original_value=row["ai_original_value"],
            submitted_value=row["submitted_value"],
            is_checked=row["is_checked"],
            checked_at=row["checked_at"],
            review_comment=row["review_comment"],
        )
        for row in rows
    ]


def list_changes(db: Session, invoice_id: UUID) -> list[CaseChange]:
    return [CaseChange(**row) for row in db.execute(LIST_CHANGES, _id(invoice_id)).mappings()]


def list_status_history(db: Session, invoice_id: UUID) -> list[CaseStatusEntry]:
    rows = db.execute(LIST_STATUS_HISTORY, _id(invoice_id)).mappings()
    return [CaseStatusEntry(**row) for row in rows]


def get_latest_recognition(db: Session, invoice_id: UUID) -> CaseRecognition | None:
    row = db.execute(GET_LATEST_RECOGNITION, _id(invoice_id)).mappings().one_or_none()
    return None if row is None else CaseRecognition(**row)


def _id(invoice_id: UUID) -> dict[str, Any]:
    return {"invoice_id": str(invoice_id)}


def _text(value: UUID | None) -> str | None:
    return None if value is None else str(value)
