"""模組名稱：app.db.repositories.invoice_repository
功能說明：以固定 SQL 存取 Schema v1.4 的發票資料（invoices、invoice_items、invoice_files）。
注意事項：
    - 表結構由 API 專案管理；本模組使用固定 SQL 與參數綁定。
    - 查詢排除無效或非使用者上傳之發票。
"""

from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_engine
from app.utils.account_display import account_display
from app.utils.format import TAIPEI_TZ

# Service 以此捕捉資料庫例外（Service 不直接依賴 SQLAlchemy）
DatabaseError = SQLAlchemyError

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
           p.display_name AS submitter_name,
           i.department_id::text AS department_id,
           d.department_name,
           f.original_filename,
           f.mime_type,
           it.item_names
    FROM public.invoices i
    LEFT JOIN public.user_profiles p ON p.user_id = i.submitted_by
    LEFT JOIN public.departments d ON d.department_id = i.department_id
    LEFT JOIN LATERAL (
        SELECT original_filename, mime_type
        FROM public.invoice_files
        WHERE invoice_id = i.invoice_id
        ORDER BY is_current DESC, version_no DESC
        LIMIT 1
    ) f ON true
    -- 列表的「品名項目」：依明細順序以「、」串接（2026-11-06：原本沒讀明細，品名一律空白）
    LEFT JOIN LATERAL (
        SELECT string_agg(item_name, '、' ORDER BY line_no) AS item_names
        FROM public.invoice_items
        WHERE invoice_id = i.invoice_id AND COALESCE(btrim(item_name), '') <> ''
    ) it ON true
    WHERE (CAST(:is_finance AS boolean) = true OR i.submitted_by::text = CAST(:user_id AS text))
      AND (CAST(:status AS text) IS NULL OR i.status = CAST(:status AS text))
      AND (
          CAST(:q AS text) = '' OR
          COALESCE(i.invoice_track, '') || COALESCE(i.invoice_number, '')
              ILIKE CAST(:q_like AS text) OR
          COALESCE(i.seller_name, '') ILIKE CAST(:q_like AS text) OR
          COALESCE(i.buyer_name, '') ILIKE CAST(:q_like AS text) OR
          COALESCE(it.item_names, '') ILIKE CAST(:q_like AS text)
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
           p.display_name AS submitter_name,
           i.department_id::text AS department_id,
           d.department_name,
           f.original_filename,
           f.mime_type
    FROM public.invoices i
    LEFT JOIN public.user_profiles p ON p.user_id = i.submitted_by
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

# 帳號與發票主查詢分開：user_accounts 啟用 RLS 且無預設 GRANT，權限不足或被 RLS 擋下時，
# 只讓「上傳者帳號」退回「未提供」，不讓發票清單或詳情整體失敗。只取 user_id 與 email。
SELECT_SUBMITTER_ACCOUNTS = text("""
    SELECT user_id::text AS user_id, email
    FROM invoiceguard_identity.user_accounts
    WHERE user_id::text = ANY(CAST(:user_ids AS text[]))
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


def _format_taipei_date(val: Any) -> str:
    """timestamptz → 台灣日期（民國年 YYY/MM/DD）；沒有時區的值視為已是台灣時間。"""
    if not val or not hasattr(val, "year"):
        return _format_date(val)
    if getattr(val, "tzinfo", None) is not None:
        val = val.astimezone(TAIPEI_TZ)
    return _format_date(val.date() if hasattr(val, "date") else val)


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
        # 資料庫沒有課稅別（草稿可為 NULL）：保留空白，不推定為應稅；F09 會回報課稅別缺漏
        tax_category = ""
        tax_type = ""

    # 列表查詢不讀整份明細，只帶 item_names（SELECT_INVOICES）；單筆查詢傳入 items
    item_desc = str(row.get("item_names") or "")
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
        "uploaded_by": row.get("submitted_by") or "",  # 缺值不填假使用者（"1" 是示範／測試身分）
        "submitter_name": row.get("submitter_name") or "",
        "submitter_account": account_display(row.get("submitter_account")),
        "department_id": row.get("department_id") or "",
        "department_name": row.get("department_name") or "",
        "format_identification": row.get("format_identification") or "",
        "invoice_number": invoice_number,
        "invoice_date": _format_date(row.get("invoice_date")),
        # 上傳日期＝案件建立日（台灣日期，民國年）；草稿沒有送出時間，所以用建立時間
        "uploaded_date": _format_taipei_date(row.get("created_at")),
        "buyer_name": row.get("buyer_name") or "",
        "seller_name": row.get("seller_name") or "",
        "seller_tax_id": row.get("seller_tax_id") or "",
        "buyer_tax_id": row.get("buyer_tax_id") or "",
        "item_description": item_desc or "",
        "quantity": qty,
        "unit_price": unit_p,
        "amount": amt,
        # 申報用原始金額：NULL 保持 None、真正的 0 保持 0、小數保持原值，由 F09 判定；net_amount／tax_amount 只供畫面顯示
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
        "db_status": status_raw,  # 資料庫的原始簽核狀態（列表判斷是否顯示「補正」用；示範資料沒有此欄位）
        "is_completed": is_completed,
        "file_name": row.get("original_filename"),
        "file_media_type": row.get("mime_type") or "image/jpeg",
        "purpose": item_desc,
        "account_code": "5601 辦公用品費",
        "deduction_code": "1 - 進貨及費用",
        "tax_type": tax_type,
        "import_confirmed_at": None,
        "filing_preview": None,
        "first_four_checked": stage >= 4,
    }


def list_submitter_accounts(db: Session, user_ids: list[str]) -> dict[str, str]:
    """讀取上傳者登入帳號（user_accounts.email）；只取 user_id 與 email。

    不處理錯誤、不管理交易：權限不足或 RLS 造成的例外由 Service 以 SAVEPOINT 隔離並退回「未提供」。

    Args:
        db: 資料庫 Session。
        user_ids: 上傳者 User ID；空值略過。

    Returns:
        {user_id: email}；查不到（含被 RLS 過濾）的 ID 不在結果中。
    """
    ids = sorted({u for u in user_ids if u})
    if not ids:
        return {}
    rows = db.execute(SELECT_SUBMITTER_ACCOUNTS, {"user_ids": ids}).mappings().all()
    return {str(r["user_id"]): str(r["email"] or "") for r in rows}


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
