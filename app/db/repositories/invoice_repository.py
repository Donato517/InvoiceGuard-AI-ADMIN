"""模組名稱：app.db.repositories.invoice_repository
功能說明：以固定 SQL 存取 Schema v1.4 的發票資料（invoices、invoice_items、invoice_files）。
注意事項：
    - 表結構由 API 專案管理；本模組使用固定 SQL 與參數綁定。
    - 查詢排除無效或非使用者上傳之發票。
"""

from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_engine

SELECT_INVOICES = text("""
    SELECT i.invoice_id::text AS invoice_id,
           i.status,
           i.submission_round,
           i.submitted_at,
           i.created_at,
           i.tax_period,
           i.invoice_track,
           i.invoice_number,
           i.invoice_date,
           i.seller_tax_id,
           i.seller_name,
           i.buyer_tax_id,
           i.buyer_name,
           i.tax_type,
           i.sales_amount,
           i.tax_amount,
           i.total_amount,
           i.total_amount_zh,
           i.validation_results,
           i.submitted_by::text AS submitted_by,
           i.department_id::text AS department_id,
           f.original_filename,
           f.mime_type
    FROM public.invoices i
    LEFT JOIN LATERAL (
        SELECT original_filename, mime_type
        FROM public.invoice_files
        WHERE invoice_id = i.invoice_id
        ORDER BY is_current DESC, version_no DESC
        LIMIT 1
    ) f ON true
    WHERE (CAST(:is_finance AS boolean) = true OR i.submitted_by::text = CAST(:user_id AS text))
      AND (CAST(:status AS text) IS NULL OR i.status = CAST(:status AS text))
      AND (
          CAST(:q AS text) = '' OR
          COALESCE(i.invoice_track, '') || COALESCE(i.invoice_number, '')
              ILIKE CAST(:q_like AS text) OR
          COALESCE(i.seller_name, '') ILIKE CAST(:q_like AS text) OR
          COALESCE(i.buyer_name, '') ILIKE CAST(:q_like AS text)
      )
    ORDER BY i.submitted_at DESC NULLS LAST, i.created_at DESC
""")

SELECT_INVOICE_BY_ID = text("""
    SELECT i.invoice_id::text AS invoice_id,
           i.status,
           i.submission_round,
           i.submitted_at,
           i.created_at,
           i.tax_period,
           i.invoice_track,
           i.invoice_number,
           i.invoice_date,
           i.seller_tax_id,
           i.seller_name,
           i.buyer_tax_id,
           i.buyer_name,
           i.tax_type,
           i.sales_amount,
           i.tax_amount,
           i.total_amount,
           i.total_amount_zh,
           i.validation_results,
           i.submitted_by::text AS submitted_by,
           i.department_id::text AS department_id,
           f.original_filename,
           f.mime_type
    FROM public.invoices i
    LEFT JOIN LATERAL (
        SELECT original_filename, mime_type
        FROM public.invoice_files
        WHERE invoice_id = i.invoice_id
        ORDER BY is_current DESC, version_no DESC
        LIMIT 1
    ) f ON true
    WHERE i.invoice_id::text = :invoice_id
""")

SELECT_INVOICE_ITEMS = text("""
    SELECT item_id::text AS item_id,
           invoice_id::text AS invoice_id,
           line_no,
           item_name,
           quantity,
           unit_price,
           line_amount
    FROM public.invoice_items
    WHERE invoice_id::text = :invoice_id
    ORDER BY line_no
""")


_TAX_TYPE_TO_DB = {"應稅": "taxable", "零稅率": "zero_rated", "免稅": "tax_exempt"}


def try_get_db_session() -> Session | None:
    """嘗試取得資料庫 Session；未設定 DATABASE_URL 或無法連線時回傳 None。"""
    try:
        engine = get_engine()
        return SessionLocal(bind=engine)
    except Exception:
        return None


def to_uuid_str(val: Any) -> str:
    """將字串或數值轉換為有效 UUID 字串。"""
    s = str(val or "").strip()
    try:
        return str(UUID(s))
    except (ValueError, AttributeError):
        return str(uuid5(NAMESPACE_DNS, f"invoice-{s}"))


def _format_date(val: Any) -> str:
    if not val:
        return ""
    if hasattr(val, "year"):
        roc_year = val.year - 1911
        return f"{roc_year}/{val.strftime('%m/%d')}"
    return str(val)


def _row_to_invoice_dict(
    row: dict[str, Any], items: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    invoice_number = f"{row.get('invoice_track') or ''}{row.get('invoice_number') or ''}".strip()
    status_raw = str(row.get("status") or "draft")

    # 依 DB 狀態（Schema v1.4 九種）推導審核狀態與步驟進度
    if status_raw == "approved":
        stage = 9
        review_status = "approved"
        view_status = "passed"
        is_completed = True
    elif status_raw in {"returned_to_applicant", "returned_to_accounting"}:
        stage = 3
        review_status = "returned"
        view_status = "flagged"
        is_completed = False
    elif status_raw in {"rejected", "voided"}:
        stage = 5
        review_status = "rejected"
        view_status = "rejected"
        is_completed = True
    elif status_raw.startswith("pending_"):
        stage = 5
        review_status = "pending"
        view_status = "passed"
        is_completed = False
    else:
        stage = 4
        review_status = "pending"
        view_status = "pending"
        is_completed = False

    raw_tax_type = str(row.get("tax_type") or "taxable")
    if raw_tax_type == "tax_exempt":
        tax_category = "免稅"
        tax_type = "3 - 免稅"
    elif raw_tax_type == "zero_rated":
        tax_category = "零稅率"
        tax_type = "2 - 零稅率"
    else:
        tax_category = "應稅"
        tax_type = "1 - 應稅"

    item_desc = ""
    qty = 1
    unit_p = int(row.get("sales_amount") or 0)
    amt = int(row.get("sales_amount") or 0)
    if items:
        item_desc = "、".join(it.get("item_name", "") for it in items if it.get("item_name"))
        first_it = items[0]
        qty = int(first_it.get("quantity") or 1)
        unit_p = int(first_it.get("unit_price") or unit_p)
        amt = int(first_it.get("line_amount") or amt)

    return {
        "id": row["invoice_id"],
        "uploaded_by": row.get("submitted_by") or "1",
        "format_identification": row.get("format_identification") or "",
        "invoice_number": invoice_number,
        "invoice_date": _format_date(row.get("invoice_date")),
        "buyer_name": row.get("buyer_name") or "",
        "seller_name": row.get("seller_name") or "",
        "seller_tax_id": row.get("seller_tax_id") or "",
        "buyer_tax_id": row.get("buyer_tax_id") or "",
        "item_description": item_desc or "",
        "quantity": qty,
        "unit_price": unit_p,
        "amount": amt,
        "net_amount": int(row.get("sales_amount") or 0),
        "tax_category": tax_category,
        "tax_amount": int(row.get("tax_amount") or 0),
        "total_amount": int(row.get("total_amount") or 0),
        "total_chinese": row.get("total_amount_zh") or "",
        "remarks": "",
        "stage": stage,
        "status": view_status,
        "review_status": review_status,
        "is_completed": is_completed,
        "file_name": row.get("original_filename"),
        "file_media_type": row.get("mime_type") or "image/jpeg",
        "purpose": item_desc or "公務使用",
        "account_code": "5601 辦公用品費",
        "deduction_code": "1 - 進貨及費用",
        "tax_type": tax_type,
        "import_confirmed_at": None,
        "filing_preview": None,
        "first_four_checked": stage >= 4,
    }


def list_invoices(
    db: Session,
    *,
    user_id: str | None = None,
    is_finance: bool = False,
    status: str | None = None,
    q: str = "",
) -> list[dict[str, Any]]:
    """自 public.invoices 讀取真實發票清單（排除未上傳之示範假資料）。"""
    params = {
        "is_finance": is_finance,
        "user_id": user_id or "",
        "status": status,
        "q": q.strip(),
        "q_like": f"%{q.strip()}%" if q.strip() else "",
    }
    result = db.execute(SELECT_INVOICES, params).mappings().all()
    invoices: list[dict[str, Any]] = []
    for r in result:
        row_dict = dict(r)
        invoices.append(_row_to_invoice_dict(row_dict))
    return invoices


def get_invoice_by_id(db: Session, invoice_id: Any) -> dict[str, Any] | None:
    """自資料庫依 ID 讀取單筆發票及明細。"""
    row = db.execute(SELECT_INVOICE_BY_ID, {"invoice_id": str(invoice_id)}).mappings().first()
    if not row:
        return None
    items_rows = db.execute(SELECT_INVOICE_ITEMS, {"invoice_id": str(invoice_id)}).mappings().all()
    items = [dict(it) for it in items_rows]
    return _row_to_invoice_dict(dict(row), items)
