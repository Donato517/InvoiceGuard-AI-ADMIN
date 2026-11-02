"""模組名稱：app.db.repositories.invoice_repository
功能說明：以固定 SQL 存取 Schema v1.4 的發票資料（invoices、invoice_items、invoice_files）。
注意事項：
    - 表結構由 API 專案管理；本模組使用固定 SQL 與參數綁定。
    - 查詢排除無效或非使用者上傳之發票。
"""

from datetime import date
from decimal import Decimal
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
           d.department_name,
           f.original_filename,
           f.mime_type
    FROM public.invoices i
    LEFT JOIN public.departments d ON d.department_id = i.department_id
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
           d.department_name,
           f.original_filename,
           f.mime_type
    FROM public.invoices i
    LEFT JOIN public.departments d ON d.department_id = i.department_id
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

UPSERT_INVOICE = text("""
    INSERT INTO public.invoices (
        invoice_id, invoice_track, invoice_number, invoice_date, tax_period,
        seller_tax_id, seller_name, buyer_tax_id, buyer_name, sales_amount,
        tax_amount, total_amount, total_amount_zh, tax_type, submission_round,
        status, validation_results, submitted_by, department_id, submitted_at
    ) VALUES (
        CAST(:invoice_id AS uuid), :invoice_track, :invoice_number, :invoice_date, :tax_period,
        :seller_tax_id, :seller_name, :buyer_tax_id, :buyer_name, :sales_amount,
        :tax_amount, :total_amount, :total_amount_zh, :tax_type, :submission_round,
        :status, CAST(:validation_results AS jsonb), CAST(:submitted_by AS uuid),
        CAST(:department_id AS uuid), now()
    )
    ON CONFLICT (invoice_id) DO UPDATE SET
        invoice_track = EXCLUDED.invoice_track,
        invoice_number = EXCLUDED.invoice_number,
        invoice_date = EXCLUDED.invoice_date,
        tax_period = EXCLUDED.tax_period,
        seller_tax_id = EXCLUDED.seller_tax_id,
        seller_name = EXCLUDED.seller_name,
        buyer_tax_id = EXCLUDED.buyer_tax_id,
        buyer_name = EXCLUDED.buyer_name,
        sales_amount = EXCLUDED.sales_amount,
        tax_amount = EXCLUDED.tax_amount,
        total_amount = EXCLUDED.total_amount,
        total_amount_zh = EXCLUDED.total_amount_zh,
        tax_type = EXCLUDED.tax_type,
        status = EXCLUDED.status,
        validation_results = EXCLUDED.validation_results,
        submitted_at = now()
    WHERE public.invoices.status = 'draft' AND public.invoices.submission_round = 0
""")


class ProtectedInvoiceError(Exception):
    """已提交（非草稿）的案件不可經 ADMIN 的核對修正路徑改寫；補正重送尚未開放（W2）。"""


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
        # 待審核（含 pending_accounting_review／pending_department_approval）絕不是已通過或已核准
        stage = 5
        review_status = "pending"
        view_status = "pending"
        is_completed = False
    else:
        stage = 4
        review_status = "pending"
        view_status = "pending"
        is_completed = False

    raw_tax_type = str(row.get("tax_type") or "")
    if raw_tax_type == "tax_exempt":
        tax_category = "免稅"
        tax_type = "3 - 免稅"
    elif raw_tax_type == "zero_rated":
        tax_category = "零稅率"
        tax_type = "2 - 零稅率"
    elif raw_tax_type == "taxable":
        tax_category = "應稅"
        tax_type = "1 - 應稅"
    else:
        # 資料庫沒有課稅別（草稿可為 NULL）：保留空白，不推測為應稅
        tax_category = ""
        tax_type = ""

    # 缺值保持空白／0，不以範例值取代；數量、單價、明細金額只取第一筆明細（小數精度另案處理）
    item_desc = ""
    qty = 0
    unit_p = 0
    amt = 0
    if items:
        item_desc = "、".join(it.get("item_name", "") for it in items if it.get("item_name"))
        first_it = items[0]
        qty = int(first_it.get("quantity") or 0)
        unit_p = int(first_it.get("unit_price") or 0)
        amt = int(first_it.get("line_amount") or 0)

    return {
        "id": row["invoice_id"],
        "db_status": status_raw,  # 資料庫原始狀態碼；核准判斷與匯出資格只看這個，不看畫面狀態
        "department_id": row.get("department_id") or "",
        "department_name": row.get("department_name") or "",
        "uploaded_by": row.get("submitted_by") or "",
        "format_identification": "三聯式手開發票",
        "invoice_number": invoice_number,
        "invoice_date": _format_date(row.get("invoice_date")),
        "buyer_name": row.get("buyer_name") or "",
        "seller_name": row.get("seller_name") or "",
        "seller_tax_id": row.get("seller_tax_id") or "",
        "buyer_tax_id": row.get("buyer_tax_id") or "",
        "item_description": item_desc,
        "quantity": qty,
        "unit_price": unit_p,
        "amount": amt,
        # 申報用原始金額：資料庫 NULL 保持 None、真正的 0 保持 0；不經 int()／or 0 轉換
        "sales_amount_db": row.get("sales_amount"),
        "tax_amount_db": row.get("tax_amount"),
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
        "purpose": item_desc,
        "account_code": "",  # 會計欄位資料契約未核定（資料庫無此欄位），不填假科目
        "deduction_code": "",
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


def save_invoice(db: Session, invoice: dict[str, Any], invoice_id: Any) -> None:
    """將核對修正後之發票儲存或更新至 public.invoices。"""
    inv_no_full = str(invoice.get("invoice_number") or "").strip()
    track = inv_no_full[:2] if len(inv_no_full) >= 2 and inv_no_full[:2].isalpha() else ""
    num = inv_no_full[2:] if track else inv_no_full
    inv_date = None
    raw_d = str(invoice.get("invoice_date") or "").strip()
    if "/" in raw_d:
        parts = raw_d.split("/")
        if len(parts) == 3:
            try:
                y = int(parts[0]) + 1911 if int(parts[0]) < 1911 else int(parts[0])
                inv_date = date(y, int(parts[1]), int(parts[2]))
            except Exception:
                inv_date = None

    uuid_id = to_uuid_str(invoice_id)
    user_uuid = to_uuid_str(invoice.get("uploaded_by", "1"))
    dept_uuid = to_uuid_str(invoice.get("department_id", "1"))

    result = db.execute(
        UPSERT_INVOICE,
        {
            "invoice_id": uuid_id,
            "invoice_track": track,
            "invoice_number": num,
            "invoice_date": inv_date,
            "tax_period": invoice.get("tax_period") or "11308",
            "seller_tax_id": invoice.get("seller_tax_id"),
            "seller_name": invoice.get("seller_name"),
            "buyer_tax_id": invoice.get("buyer_tax_id"),
            "buyer_name": invoice.get("buyer_name"),
            "sales_amount": Decimal(str(invoice.get("net_amount", 0))),
            "tax_amount": Decimal(str(invoice.get("tax_amount", 0))),
            "total_amount": Decimal(str(invoice.get("total_amount", 0))),
            "total_amount_zh": invoice.get("total_chinese", ""),
            "tax_type": _TAX_TYPE_TO_DB.get(str(invoice.get("tax_category") or "應稅"), "taxable"),
            "submission_round": 0,
            "status": "draft",
            "validation_results": "{}",
            "submitted_by": user_uuid,
            "department_id": dept_uuid,
        },
    )
    # 衝突且不是草稿時 WHERE 條件不成立，不會更新任何列：視為被保護，絕不靜默成功
    if result.rowcount == 0:
        raise ProtectedInvoiceError(uuid_id)
