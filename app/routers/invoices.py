"""
模組名稱：app.routers.invoices
功能說明：發票審核——列表、詳情、人工複核、新增發票與 9 步驟發票作業流程。
路由：/invoices
注意事項：
    - 本路由需管理者權限（require_admin）。
    - 步驟 1～9：上傳發票、AI OCR 辨識、資料核對、系統自動檢核、財務審核、產生申報檔、匯入確認、自動產生傳票、匯出結果。
    - 步驟 6 與申報資料頁的檢核、81 字元申報資料與批次產檔由 format21_service（F09）負責；
      發票資料目前仍為示範資料，經 _to_filing_snapshot() 轉為 Schema v1.5 欄位後交給 F09。
"""

import re
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.constants import INVOICE_FLOW_STEPS, INVOICE_STATUS_LABEL
from app.core.deps import require_admin
from app.core.templating import templates
from app.routers.e_admin import session_evidence
from app.schemas.e_admin import Action, DeniedError, Target, UnauthenticatedError
from app.services.company_profile_service import HardcodedCompanyProfileProvider
from app.services.e_admin_service import INVOICES, EModule, get_e_module
from app.services.format21_service import (
    build_format21_file,
    check_snapshot,
    format_record_line,
    summarize_401,
    validate_profile,
)

Module = Annotated[EModule, Depends(get_e_module)]


def _is_finance_department(departments: tuple[Any, ...], selected_department_id: Any) -> bool:
    for dept in departments:
        if getattr(dept, "department_id", None) == selected_department_id:
            name = getattr(dept, "department_name", "") or ""
            code = (getattr(dept, "department_code", "") or "").upper()
            if "財務" in name or code in {"FINANCE", "FIN", "ACCOUNTING", "ACC"}:
                return True
    return False


def require_invoice_access(request: Request, module: Module) -> None:
    """驗證發票作業存取權限（僅財務部開放步驟 5～9 及財會檢核／申報資料／會計傳票，其餘部門僅開放前 4 步驟）。"""
    override = request.app.dependency_overrides.get(require_admin)
    if override is not None:
        admin_user = override(request)
        user_id = getattr(admin_user, "id", None) or getattr(
            getattr(request.state, "admin", None), "id", "1"
        )
        request.state.invoice_user_id = str(user_id)
        request.state.is_finance_workspace = True
        request.state.invoice_max_step = 9
        return
    token = session_evidence(request)
    if token is not None and module.access is not None:
        identity = module.identity(token)
        module.check(
            identity,
            INVOICES,
            Action.VIEW,
            Target(identity.selected_department_id),
        )
        request.state.e_configuration_authenticated = True
        request.state.e_configuration_menu = module.sidebar_menu(identity)
        departments = module.departments(identity)
        request.state.header_departments = departments
        request.state.header_selected_department_id = identity.selected_department_id
        request.state.invoice_user_id = str(identity.user_id)
        is_finance = _is_finance_department(departments, identity.selected_department_id)
        request.state.is_finance_workspace = is_finance
        request.state.invoice_max_step = 9 if is_finance else 4
        return
    raise UnauthenticatedError


router = APIRouter(
    prefix="/invoices",
    tags=["invoices"],
    dependencies=[Depends(require_invoice_access)],
)


def _invoice_chinese_amount(amount: int | float) -> str:
    """將數值金額轉為繁體中文國字大寫金額（例如 6300 -> 陸仟參佰元整）。"""
    cents = round(float(amount or 0) * 100)
    if cents < 0 or cents > 99_999_999_999_999:
        return ""
    digits = "零壹貳參肆伍陸柒捌玖"
    small = ["", "拾", "佰", "仟"]
    large = ["", "萬", "億", "兆"]
    integer = cents // 100
    groups: list[int] = []
    while integer:
        groups.append(integer % 10000)
        integer //= 10000
    result = ""
    between = False
    for index in range(len(groups) - 1, -1, -1):
        group = groups[index]
        if not group:
            if result:
                between = True
            continue
        if result and (between or group < 1000):
            result += "零"
        between = False
        part = ""
        zero = False
        for p in range(3, -1, -1):
            digit = (group // (10**p)) % 10
            if not digit:
                if part:
                    zero = True
                continue
            if zero:
                part += "零"
            zero = False
            part += digits[digit] + small[p]
        result += part + large[index]
    result = (result or "零") + "元"
    jiao = (cents % 100) // 10
    fen = cents % 10
    if not jiao and not fen:
        return result + "整"
    if jiao:
        result += digits[jiao] + "角"
    if fen:
        result += ("零" if not jiao else "") + digits[fen] + "分"
    return result


def _ensure_invoice_14_fields(invoice: dict[str, Any]) -> dict[str, Any]:
    """補齊並同步發票 14 項核對欄位預設值。"""
    invoice.setdefault("uploaded_by", "1")
    invoice.setdefault("format_identification", "三聯式手開發票")
    invoice.setdefault("invoice_number", "")
    invoice.setdefault("invoice_date", "113/08/15")
    invoice.setdefault("buyer_name", "範例科技股份有限公司")
    invoice.setdefault("buyer_tax_id", "12345678")
    invoice.setdefault("item_description", invoice.get("purpose", ""))
    invoice.setdefault("purpose", invoice["item_description"])
    net = int(invoice.get("net_amount", 0) or 0)
    tax = int(invoice.get("tax_amount", 0) or 0)
    invoice.setdefault("quantity", 1)
    invoice.setdefault("unit_price", net)
    invoice.setdefault("amount", net)
    raw_tax_type = str(invoice.get("tax_type", "1 - 應稅") or "")
    default_tax_cat = (
        "免稅" if "免稅" in raw_tax_type else ("零稅率" if "零稅率" in raw_tax_type else "應稅")
    )
    invoice.setdefault("tax_category", default_tax_cat)
    total = int(invoice.get("total_amount", net + tax) or (net + tax))
    invoice["total_amount"] = total
    if not invoice.get("total_chinese"):
        invoice["total_chinese"] = _invoice_chinese_amount(total)
    invoice.setdefault("remarks", "")
    invoice.setdefault("seller_name", "範例銷貨人")
    invoice.setdefault("seller_tax_id", "24681357")
    return invoice


def _build_14_fields_rows(invoice: dict[str, Any]) -> list[dict[str, Any]]:
    """將發票 14 項核對欄位整理為顯示列清單（供 OCR 預覽、財務審核與詳情頁檢視）。"""
    _ensure_invoice_14_fields(invoice)
    seller_tax = invoice.get("seller_tax_id", "")
    seller_display = (
        f"{invoice['seller_name']} ／ 統編：{seller_tax}" if seller_tax else invoice["seller_name"]
    )
    return [
        {"index": 1, "label": "發票聯式／識別", "value": invoice["format_identification"]},
        {"index": 2, "label": "發票字軌號碼", "value": invoice["invoice_number"]},
        {"index": 3, "label": "發票日期", "value": invoice["invoice_date"]},
        {"index": 4, "label": "買受人名稱", "value": invoice["buyer_name"]},
        {"index": 5, "label": "買受人統編", "value": invoice["buyer_tax_id"]},
        {"index": 6, "label": "品名／用途", "value": invoice["item_description"]},
        {"index": 7, "label": "數量", "value": str(invoice["quantity"])},
        {"index": 8, "label": "單價", "value": f"${int(invoice['unit_price']):,}"},
        {"index": 9, "label": "金額", "value": f"${int(invoice['amount']):,}"},
        {"index": 10, "label": "銷售額合計", "value": f"${int(invoice['net_amount']):,}"},
        {
            "index": 11,
            "label": "課稅別與稅額",
            "value": f"{invoice['tax_category']} ／ 稅額：${int(invoice['tax_amount']):,}",
        },
        {
            "index": 12,
            "label": "總計／國字大寫",
            "value": f"${int(invoice['total_amount']):,} ／ {invoice['total_chinese']}",
        },
        {"index": 13, "label": "備註", "value": invoice["remarks"] or "—"},
        {"index": 14, "label": "銷貨人", "value": seller_display},
    ]


def _check_invoice_access(
    request: Request,
    invoice: dict[str, Any],
    *,
    require_owner: bool = False,
) -> None:
    """驗證發票存取權：財務部可檢視及處理所有發票；非財務部僅限檢視及處理本人上傳之發票。"""
    current_user_id = str(getattr(request.state, "invoice_user_id", "1"))
    is_owner = str(invoice.get("uploaded_by", "1")) == current_user_id
    is_finance = bool(getattr(request.state, "is_finance_workspace", False))
    if is_finance:
        return
    if not is_owner:
        raise DeniedError


# 示範發票資料庫（對應原型 seedInvoices）
_INVOICE_STORE: list[dict[str, Any]] = [
    {
        "id": 1,
        "uploaded_by": "1",
        "format_identification": "三聯式手開發票",
        "invoice_number": "AB12345678",
        "invoice_date": "113/08/15",
        "buyer_name": "範例科技股份有限公司",
        "seller_name": "範例銷貨人",
        "seller_tax_id": "24681357",
        "buyer_tax_id": "12345678",
        "item_description": "辦公耗材與文具用品",
        "quantity": 1,
        "unit_price": 6000,
        "amount": 6000,
        "net_amount": 6000,
        "tax_category": "應稅",
        "tax_amount": 300,
        "total_amount": 6300,
        "total_chinese": "陸仟參佰元整",
        "remarks": "",
        "stage": 9,
        "status": "passed",
        "review_status": "approved",
        "is_completed": True,
        "file_name": "invoice_001.jpg",
        "purpose": "供行政與財務部門日常辦公使用",
        "account_code": "5601 辦公用品費",
        "deduction_code": "1 - 進貨及費用",
        "tax_type": "1 - 應稅",
        "import_confirmed_at": datetime(2026, 8, 16, 14, 0, tzinfo=UTC),
        "filing_preview": "2112345678AB12345678000000600000000003001",
    },
    {
        "id": 2,
        "uploaded_by": "1",
        "format_identification": "三聯式手開發票",
        "invoice_number": "AB12345679",
        "invoice_date": "113/08/15",
        "buyer_name": "範例科技股份有限公司",
        "seller_name": "範例銷貨人",
        "seller_tax_id": "24681357",
        "buyer_tax_id": "12345678",
        "item_description": "影印紙與印表機碳粉",
        "quantity": 1,
        "unit_price": 2000,
        "amount": 2000,
        "net_amount": 2000,
        "tax_category": "應稅",
        "tax_amount": 100,
        "total_amount": 2100,
        "total_chinese": "貳仟壹佰元整",
        "remarks": "",
        "stage": 9,
        "status": "passed",
        "review_status": "approved",
        "is_completed": True,
        "file_name": "invoice_002.jpg",
        "purpose": "資訊設備耗材補充",
        "account_code": "5601 辦公用品費",
        "deduction_code": "1 - 進貨及費用",
        "tax_type": "1 - 應稅",
        "import_confirmed_at": datetime(2026, 8, 16, 15, 30, tzinfo=UTC),
        "filing_preview": "2112345678AB12345679000000200000000001001",
    },
    {
        "id": 3,
        "uploaded_by": "1",
        "format_identification": "三聯式手開發票",
        "invoice_number": "AB12345680",
        "invoice_date": "113/08/15",
        "buyer_name": "範例科技股份有限公司",
        "seller_name": "範例銷貨人",
        "seller_tax_id": "24681357",
        "buyer_tax_id": "12345678",
        "item_description": "茶水間咖啡豆與茶包耗材",
        "quantity": 1,
        "unit_price": 1500,
        "amount": 1500,
        "net_amount": 1500,
        "tax_category": "應稅",
        "tax_amount": 75,
        "total_amount": 1575,
        "total_chinese": "壹仟伍佰柒拾伍元整",
        "remarks": "",
        "stage": 4,
        "status": "flagged",
        "review_status": "pending",
        "is_completed": False,
        "file_name": "invoice_003.jpg",
        "purpose": "供辦公室茶水間使用",
        "account_code": "5602 什支",
        "deduction_code": "1 - 進貨及費用",
        "tax_type": "1 - 應稅",
        "import_confirmed_at": None,
        "filing_preview": None,
    },
    {
        "id": 4,
        "uploaded_by": "1",
        "format_identification": "三聯式手開發票",
        "invoice_number": "AB12345681",
        "invoice_date": "113/08/15",
        "buyer_name": "範例科技股份有限公司",
        "seller_name": "範例銷貨人",
        "seller_tax_id": "24681357",
        "buyer_tax_id": "12345678",
        "item_description": "高階人體工學辦公椅",
        "quantity": 1,
        "unit_price": 12000,
        "amount": 12000,
        "net_amount": 12000,
        "tax_category": "應稅",
        "tax_amount": 600,
        "total_amount": 12600,
        "total_chinese": "壹萬貳仟陸佰元整",
        "remarks": "",
        "stage": 5,
        "status": "flagged",
        "review_status": "pending",
        "is_completed": False,
        "file_name": "invoice_004.jpg",
        "purpose": "研發人員辦公設備添購",
        "account_code": "1401 辦公設備",
        "deduction_code": "2 - 固定資產",
        "tax_type": "1 - 應稅",
        "import_confirmed_at": None,
        "filing_preview": None,
    },
    {
        "id": 5,
        "uploaded_by": "1",
        "format_identification": "三聯式手開發票",
        "invoice_number": "AB12345682",
        "invoice_date": "113/08/15",
        "buyer_name": "範例科技股份有限公司",
        "seller_name": "範例銷貨人",
        "seller_tax_id": "24681357",
        "buyer_tax_id": "12345678",
        "item_description": "網路線材與 RJ45 接頭",
        "quantity": 1,
        "unit_price": 800,
        "amount": 800,
        "net_amount": 800,
        "tax_category": "應稅",
        "tax_amount": 40,
        "total_amount": 840,
        "total_chinese": "捌佰肆拾元整",
        "remarks": "",
        "stage": 6,
        "status": "passed",
        "review_status": "approved",
        "is_completed": False,
        "file_name": "invoice_005.jpg",
        "purpose": "機房線路整修備品",
        "account_code": "5605 修繕費",
        "deduction_code": "1 - 進貨及費用",
        "tax_type": "1 - 應稅",
        "import_confirmed_at": None,
        "filing_preview": "2112345678AB12345682000000080000000000401",
    },
]


# 暫存尚未完成「步驟 3：採購核對修正」的新增發票草稿（不會寫入發票清單與資料庫）
_DRAFT_INVOICE_STORE: dict[int, dict[str, Any]] = {}


def _find_invoice(invoice_id: int) -> dict[str, Any] | None:
    return next((i for i in _INVOICE_STORE if i["id"] == invoice_id), None)


def _find_flow_invoice(invoice_id: int) -> dict[str, Any] | None:
    return _find_invoice(invoice_id) or _DRAFT_INVOICE_STORE.get(invoice_id)


def _build_flow_steps(
    current_step: int,
    invoice_id: int,
    is_completed: bool,
    *,
    max_stage: int = 9,
    has_file_bytes: bool = False,
    allowed_max_step: int = 9,
) -> list[dict[str, Any]]:
    """建立發票作業步驟的中繼資料（非財務部僅顯示前 4 步驟，不顯示步驟 5～9）。"""
    steps = []
    for idx, label in enumerate(INVOICE_FLOW_STEPS[:allowed_max_step], start=1):
        if is_completed:
            state = "active" if idx == current_step else "done"
        elif idx == current_step:
            state = "active"
        elif idx <= max_stage:
            state = "done"
        else:
            state = "locked"

        # 因資安考量不儲存發票影像，已完成核對修正且無暫存圖檔時，步驟 1、2 僅顯示完成勾號不再連結返回
        can_navigate = (
            state != "locked" and idx <= allowed_max_step and (has_file_bytes or idx >= 3)
        )
        steps.append(
            {
                "number": idx,
                "label": label,
                "state": state,
                "url": f"/invoices/{invoice_id}/flow?step={idx}" if can_navigate else None,
            }
        )
    return steps


def _build_checks(invoice: dict[str, Any]) -> list[dict[str, Any]]:
    """產生系統自動檢核項目（步驟 4）。"""
    inv_no = invoice.get("invoice_number", "")
    seller = invoice.get("seller_tax_id", "")
    buyer = invoice.get("buyer_tax_id", "")
    date_str = invoice.get("invoice_date", "")
    net = invoice.get("net_amount", 0)
    tax = invoice.get("tax_amount", 0)

    return [
        {
            "label": "發票字軌格式（二碼英文 + 八碼數字）",
            "ok": len(inv_no) == 10 and inv_no[:2].isalpha() and inv_no[2:].isdigit(),
        },
        {"label": "賣方統一編號位數（8 位數字）", "ok": len(seller) == 8 and seller.isdigit()},
        {"label": "買方統一編號相符", "ok": buyer == "12345678"},
        {"label": "發票開立日期格式（民國年/月/日）", "ok": len(date_str) == 9 and "/" in date_str},
        {"label": "營業稅額計算正確（5% 稅率）", "ok": tax == round(net * 0.05)},
        {"label": "非重複申報發票", "ok": True},
    ]


def _build_voucher(invoice: dict[str, Any]) -> dict[str, Any]:
    """產生會計傳票分錄（步驟 8）。"""
    net = invoice.get("net_amount", 0)
    tax = invoice.get("tax_amount", 0)
    total = net + tax
    account = invoice.get("account_code") or "5601 辦公用品費"

    lines = [
        {"account": account, "debit": net, "credit": 0},
        {"account": "1415 進項稅額", "debit": tax, "credit": 0},
        {"account": "2202 應付帳款", "debit": 0, "credit": total},
    ]
    return {
        "lines": lines,
        "total_debit": net + tax,
        "total_credit": total,
        "balanced": (net + tax) == total,
    }


def _render_invoice_list_view(
    request: Request,
    *,
    section: str = "invoices",
    status: str | None = None,
    q: str = "",
) -> HTMLResponse:
    _DRAFT_INVOICE_STORE.clear()
    current_user_id = str(getattr(request.state, "invoice_user_id", "1"))
    is_finance = bool(getattr(request.state, "is_finance_workspace", False))
    section_config = {
        "invoices": (
            "發票管理",
            "/invoices",
            lambda i: is_finance or str(i.get("uploaded_by", "1")) == current_user_id,
        ),
        "review": (
            "財會檢核",
            "/invoices/review",
            lambda i: not i["is_completed"] and 3 <= int(i.get("stage", 4)) <= 5,
        ),
        "filing": (
            "申報資料",
            "/invoices/filing",
            lambda i: not i["is_completed"] and 6 <= int(i.get("stage", 4)) <= 7,
        ),
        "voucher": (
            "會計傳票",
            "/invoices/voucher",
            lambda i: int(i.get("stage", 4)) >= 8,
        ),
    }
    section_title, active_route, section_filter = section_config[section]
    request.state.active_sidebar_route = active_route
    base_invoices = [_ensure_invoice_14_fields(i) for i in _INVOICE_STORE if section_filter(i)]
    invoices = list(base_invoices)

    if status and status in INVOICE_STATUS_LABEL:
        invoices = [i for i in invoices if i["status"] == status]

    if q.strip():
        search_term = q.strip().lower()
        invoices = [
            i
            for i in invoices
            if search_term in i["invoice_number"].lower()
            or search_term in i["item_description"].lower()
        ]

    pending_count = sum(1 for i in base_invoices if not i["is_completed"])
    passed_count = sum(1 for i in base_invoices if i["status"] == "passed")
    flagged_count = sum(1 for i in base_invoices if i["status"] == "flagged")

    counts = {
        "total": len(base_invoices),
        "pending": pending_count,
        "passed": passed_count,
        "flagged": flagged_count,
    }

    return templates.TemplateResponse(
        request,
        "pages/invoices/list.html",
        {
            "invoices": invoices,
            "status": status,
            "q": q,
            "counts": counts,
            "section_title": section_title,
            "max_flow_step": getattr(request.state, "invoice_max_step", 9),
        },
    )


@router.get("", response_class=HTMLResponse)
def invoice_list(
    request: Request,
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """🗂️ 發票管理列表：財務部可檢視所有發票，非財務部僅限檢視本人上傳發票。"""
    return _render_invoice_list_view(request, section="invoices", status=status, q=q)


@router.get("/upload")
def invoice_upload_entry(request: Request) -> RedirectResponse:
    """📤 發票上傳入口：建立暫存發票草稿並進入步驟 1（採購上傳發票）。"""
    return create_new_invoice(request)


@router.get("/review", response_class=HTMLResponse)
def invoice_review_list(
    request: Request,
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """🔍 財會檢核列表（僅財務部工作區開放）。"""
    if getattr(request.state, "invoice_max_step", 9) < 9:
        raise DeniedError
    return _render_invoice_list_view(request, section="review", status=status, q=q)


# ---- 申報檔（F09）----

# TODO(StacieChen): 公司主檔確定後改由設定讀取（見 F_Schema補充建議_v1.5.md §5）
_FILING_PROFILE = HardcodedCompanyProfileProvider()
_TAX_CATEGORY_TO_TYPE = {"應稅": "taxable", "零稅率": "zero_rated", "免稅": "tax_exempt"}
_ROC_DATE = re.compile(r"(\d{2,3})/(\d{1,2})/(\d{1,2})")
_REPORT_PERIOD = re.compile(r"\d{5}")


def _roc_to_iso(value: object) -> str:
    """示範資料的民國日期（113/08/15）轉 ISO；格式不符原樣交給 F09 回報錯誤。"""
    text = str(value or "").strip()
    match = _ROC_DATE.fullmatch(text)
    if not match:
        return text
    year, month, day = (int(part) for part in match.groups())
    return f"{year + 1911:04d}-{month:02d}-{day:02d}"


def _to_filing_snapshot(invoice: dict[str, Any]) -> dict[str, Any]:
    """將示範發票轉為 F09 讀取的 Schema v1.5 欄位，缺值不補預設，交由 F09 檢核。"""
    # TODO(StacieChen): 接上資料庫後改由 repository 讀 invoices，刪除此轉換
    number = str(invoice.get("invoice_number") or "").strip().upper()
    approved = invoice.get("review_status") == "approved" or invoice.get("status") == "passed"
    deduction = str(invoice.get("deduction_code") or "").strip()[:1]
    category = invoice.get("tax_category")
    return {
        "invoice_id": str(invoice["id"]),
        "invoice_track": number[:2],
        "invoice_number": number[2:],
        "invoice_date": _roc_to_iso(invoice.get("invoice_date")),
        "seller_tax_id": invoice.get("seller_tax_id"),
        "buyer_tax_id": invoice.get("buyer_tax_id"),
        "sales_amount": invoice.get("net_amount"),
        "tax_amount": invoice.get("tax_amount"),
        "tax_type": _TAX_CATEGORY_TO_TYPE.get(str(category), category),
        "status": "approved" if approved else str(invoice.get("status") or ""),
        "deduction_code": deduction if deduction and deduction in "1234" else "",
    }


def _in_date_range(iso_date: str, from_date: str, to_date: str) -> bool:
    """發票日期在起訖日內；日期無法判讀時保留，讓檢核結果顯示在清單上。"""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso_date):
        return True
    return (not from_date or iso_date >= from_date) and (not to_date or iso_date <= to_date)


def _build_batch_filing_context(
    selected_period: str = "11308",
    from_date: str = "2024-07-01",
    to_date: str = "2024-08-31",
) -> dict[str, Any]:
    """建立批次申報檔卡片所需資料；每列的檢核結果來自 F09。"""
    periods = [
        ("11308", "113 年 07-08 期"),
        ("11306", "113 年 05-06 期"),
        ("11304", "113 年 03-04 期"),
        ("11302", "113 年 01-02 期"),
        ("11212", "112 年 11-12 期"),
        ("11210", "112 年 09-10 期"),
    ]
    if not any(code == selected_period for code, _ in periods):
        periods.insert(0, (selected_period, f"{selected_period} 期"))

    profile = _FILING_PROFILE.get_profile()
    company_issues = [issue.message for issue in validate_profile(profile)]
    rows: list[dict[str, Any]] = []
    passed_records = []
    dept_map = {"1": "採購部", "2": "行銷部", "3": "採購部", "4": "資訊部", "5": "研發部"}

    for inv in _INVOICE_STORE:
        snapshot = _to_filing_snapshot(inv)
        if not _in_date_range(snapshot["invoice_date"], from_date, to_date):
            continue
        record, issues = check_snapshot(snapshot, profile, selected_period)
        exportable = not issues and not company_issues
        if exportable:
            passed_records.append(record)
        rows.append(
            {
                "id": inv["id"],
                "invoice_number": inv.get("invoice_number", ""),
                "invoice_date": inv.get("invoice_date", ""),
                "department_name": inv.get("department_name")
                or dept_map.get(str(inv["id"]), "採購部"),
                "sales": int(inv.get("net_amount", 0)),
                "tax": int(inv.get("tax_amount", 0)),
                "checked": exportable,
                "issues": [{"code": i.code, "message": i.message} for i in issues],
            }
        )

    checked_rows = [r for r in rows if r["checked"]]
    sales_amount = sum(r["sales"] for r in checked_rows)
    tax_amount = sum(r["tax"] for r in checked_rows)

    return {
        "period": selected_period,
        "periods": periods,
        "from_date": from_date,
        "to_date": to_date,
        "rows": rows,
        "count": len(checked_rows),
        "sales_amount": sales_amount,
        "tax_amount": tax_amount,
        "total_amount": sales_amount + tax_amount,
        "company_id": profile.ban,
        "company_issues": company_issues,
        "totals_401": {
            field: int(amount) for field, amount in summarize_401(passed_records).items()
        },
    }


def _single_filing_check(invoice: dict[str, Any], period: str) -> dict[str, Any]:
    """步驟 6 單張檢核：問題清單與 81 字元預覽（有問題時不產生預覽）。"""
    record, issues = check_snapshot(
        _to_filing_snapshot(invoice), _FILING_PROFILE.get_profile(), period
    )
    return {
        "issues": [{"code": i.code, "message": i.message} for i in issues],
        "preview_line": None if issues else format_record_line(record, serial=1),
    }


@router.get("/filing", response_class=HTMLResponse)
def invoice_filing_list(
    request: Request,
    period: Annotated[str, Query()] = "11308",
    from_date: Annotated[str, Query()] = "2024-07-01",
    to_date: Annotated[str, Query()] = "2024-08-31",
) -> HTMLResponse:
    """📑 申報資料（批次產生申報檔，僅財務部工作區開放）。"""
    if getattr(request.state, "invoice_max_step", 9) < 9:
        raise DeniedError
    if not _REPORT_PERIOD.fullmatch(period):
        raise HTTPException(status_code=400, detail="申報所屬期格式錯誤")
    request.state.active_sidebar_route = "/invoices/filing"
    batch_filing = _build_batch_filing_context(
        selected_period=period, from_date=from_date, to_date=to_date
    )
    return templates.TemplateResponse(
        request,
        "pages/invoices/filing.html",
        {
            "batch_filing": batch_filing,
        },
    )


@router.post("/filing/export")
def export_batch_filing(
    request: Request,
    period: Annotated[str, Form()] = "11308",
    invoice_ids: Annotated[list[int] | None, Form()] = None,
) -> Response:
    """批次產生並下載格式 21 申報檔（每期一檔，檔名為本公司統編.TXT）。

    任一筆未通過 F09 檢核即整批不產檔，回 400 並列出原因。
    """
    if getattr(request.state, "invoice_max_step", 9) < 6:
        raise DeniedError
    if not _REPORT_PERIOD.fullmatch(period):
        raise HTTPException(status_code=400, detail="申報所屬期格式錯誤")
    ids = invoice_ids or []
    if not ids:
        raise HTTPException(status_code=400, detail="請至少勾選一筆發票")
    selected_invoices = [i for i in _INVOICE_STORE if int(i["id"]) in ids]
    if not selected_invoices:
        raise HTTPException(status_code=400, detail="找不到勾選的發票")

    result = build_format21_file(
        [_to_filing_snapshot(i) for i in selected_invoices], _FILING_PROFILE, period
    )
    if result.content is None or result.filename is None:
        numbers = {
            str(i["id"]): i.get("invoice_number") or "（未填號碼）" for i in selected_invoices
        }
        problems = "；".join(
            f"{numbers.get(key, '公司資料')}：{issues[0].message}"
            for key, issues in result.issues.items()
        )
        raise HTTPException(status_code=400, detail=f"有資料未通過檢核，未產生申報檔。{problems}")

    encoded_fn = quote(result.filename)
    return Response(
        content=result.content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_fn}"},
    )


@router.get("/voucher", response_class=HTMLResponse)
def invoice_voucher_list(
    request: Request,
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """🧾 會計傳票列表（僅財務部工作區開放）。"""
    if getattr(request.state, "invoice_max_step", 9) < 9:
        raise DeniedError
    return _render_invoice_list_view(request, section="voucher", status=status, q=q)


@router.get("/new")
def create_new_invoice(request: Request) -> RedirectResponse:
    """建立暫存發票草稿並進入步驟 1（上傳發票）；未完成步驟 3 採購核對修正前不寫入發票清單。"""
    _DRAFT_INVOICE_STORE.clear()
    new_id = max((int(i["id"]) for i in _INVOICE_STORE), default=0) + 1
    current_user_id = str(getattr(request.state, "invoice_user_id", "1"))
    new_invoice: dict[str, Any] = _ensure_invoice_14_fields(
        {
            "id": new_id,
            "uploaded_by": current_user_id,
            "format_identification": "三聯式手開發票",
            "invoice_number": f"AB{12345677 + new_id}",
            "invoice_date": "113/08/15",
            "buyer_name": "範例科技股份有限公司",
            "seller_name": "範例銷貨人",
            "seller_tax_id": "24681357",
            "buyer_tax_id": "12345678",
            "item_description": "辦公用品",
            "quantity": 1,
            "unit_price": 6000,
            "amount": 6000,
            "net_amount": 6000,
            "tax_category": "應稅",
            "tax_amount": 300,
            "total_amount": 6300,
            "total_chinese": "陸仟參佰元整",
            "remarks": "",
            "stage": 1,
            "status": "pending",
            "review_status": "pending",
            "is_completed": False,
            "file_name": None,
            "purpose": "供辦公室日常使用",
            "account_code": "5601 辦公用品費",
            "deduction_code": "1 - 進貨及費用",
            "tax_type": "1 - 應稅",
            "import_confirmed_at": None,
            "filing_preview": None,
        }
    )
    _DRAFT_INVOICE_STORE[new_id] = new_invoice
    return RedirectResponse(f"/invoices/{new_id}/flow?step=1", status_code=303)


@router.get("/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: int) -> HTMLResponse:
    """發票審核詳情：辨識結果、AI 判斷理由、核准 / 駁回。"""
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")
    _check_invoice_access(request, invoice)
    fields_14 = _build_14_fields_rows(invoice)

    return templates.TemplateResponse(
        request,
        "pages/invoices/detail.html",
        {
            "invoice_id": invoice_id,
            "invoice": invoice,
            "fields_14": fields_14,
            "max_flow_step": getattr(request.state, "invoice_max_step", 9),
        },
    )


def _render_flow_page(
    request: Request,
    invoice: dict[str, Any],
    step: int,
    *,
    error: str | None = None,
) -> HTMLResponse:
    _ensure_invoice_14_fields(invoice)
    fields_14 = _build_14_fields_rows(invoice)
    invoice_id = int(invoice["id"])
    max_flow_step = getattr(request.state, "invoice_max_step", 9)
    if max_flow_step < 9 or step <= 3:
        request.state.active_sidebar_route = "/invoices/upload"
    elif step <= 5:
        request.state.active_sidebar_route = "/invoices/review"
    elif step <= 7:
        request.state.active_sidebar_route = "/invoices/filing"
    else:
        request.state.active_sidebar_route = "/invoices/voucher"
    steps = _build_flow_steps(
        step,
        invoice_id,
        invoice["is_completed"],
        max_stage=int(invoice.get("stage", step)),
        has_file_bytes=bool(invoice.get("file_bytes")),
        allowed_max_step=max_flow_step,
    )
    checks = _build_checks(invoice)
    voucher = _build_voucher(invoice)

    filing = None
    if invoice.get("filing_preview"):
        filing = {
            "file_name": f"{invoice['invoice_number']}.txt",
            "preview_text": invoice["filing_preview"],
        }

    batch_filing = _build_batch_filing_context()
    single_filing = _single_filing_check(invoice, batch_filing["period"])

    return templates.TemplateResponse(
        request,
        "pages/invoices/flow.html",
        {
            "invoice": invoice,
            "fields_14": fields_14,
            "step": step,
            "steps": steps,
            "checks": checks,
            "voucher": voucher,
            "filing": filing,
            "single_filing": single_filing,
            "batch_filing": batch_filing,
            "error": error,
            "max_flow_step": max_flow_step,
        },
    )


@router.get("/{invoice_id}/flow", response_class=HTMLResponse)
def invoice_flow(
    request: Request,
    invoice_id: int,
    step: Annotated[int, Query(ge=1, le=9)] = 4,
) -> HTMLResponse:
    """發票作業流程（步驟 1～9；採購部僅開放步驟 1～4）。"""
    max_flow_step = getattr(request.state, "invoice_max_step", 9)
    if step > max_flow_step:
        raise DeniedError
    invoice = _find_flow_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")
    _check_invoice_access(request, invoice)

    return _render_flow_page(request, invoice, step)


@router.post("/{invoice_id}/flow/upload", response_model=None)
async def upload_invoice_file(
    request: Request,
    invoice_id: int,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    """步驟 1：上傳發票檔案並進入步驟 2（OCR 辨識預覽）；此時僅暫存於記憶體，尚未寫入發票清單。"""
    invoice = _find_flow_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")
    _check_invoice_access(request, invoice, require_owner=True)

    if file and file.filename:
        raw_bytes = await file.read()
        invoice["file_name"] = file.filename
        invoice["file_bytes"] = raw_bytes
        content_type = file.content_type or ""
        if file.filename.lower().endswith(".pdf") or "pdf" in content_type.lower():
            invoice["file_media_type"] = "application/pdf"
        elif file.filename.lower().endswith(".png") or "png" in content_type.lower():
            invoice["file_media_type"] = "image/png"
        else:
            invoice["file_media_type"] = "image/jpeg"

    if not invoice.get("file_name"):
        return _render_flow_page(
            request,
            invoice,
            1,
            error="請先選擇或拖曳發票檔案後再進行 AI OCR 辨識。",
        )

    invoice["stage"] = max(invoice.get("stage", 1), 2)
    return RedirectResponse(f"/invoices/{invoice_id}/flow?step=2", status_code=303)


@router.get("/{invoice_id}/file")
def get_invoice_file(request: Request, invoice_id: int) -> Response:
    """取得當次作業暫存之發票檔案預覽內容。"""
    invoice = _find_flow_invoice(invoice_id)
    if not invoice or not invoice.get("file_bytes"):
        raise HTTPException(status_code=404, detail="找不到發票檔案")
    _check_invoice_access(request, invoice, require_owner=True)
    return Response(
        content=invoice["file_bytes"],
        media_type=invoice.get("file_media_type", "image/jpeg"),
    )


def _commit_draft_after_correction(invoice_id: int, invoice: dict[str, Any]) -> None:
    """完成步驟 3 採購核對修正後，清除暫存影像並將發票正式寫入發票清單。"""
    invoice.pop("file_bytes", None)
    invoice.pop("file_media_type", None)
    invoice["stage"] = max(invoice.get("stage", 3), 4)
    if invoice_id in _DRAFT_INVOICE_STORE:
        _DRAFT_INVOICE_STORE.pop(invoice_id, None)
        if not _find_invoice(invoice_id):
            _INVOICE_STORE.insert(0, invoice)


@router.post("/{invoice_id}/flow/correct")
def correct_invoice_fields(
    request: Request,
    invoice_id: int,
    invoice_number: Annotated[str, Form()],
    invoice_date: Annotated[str, Form()],
    format_identification: Annotated[str | None, Form()] = None,
    buyer_name: Annotated[str | None, Form()] = None,
    buyer_tax_id: Annotated[str | None, Form()] = None,
    item_description: Annotated[str | None, Form()] = None,
    quantity: Annotated[int | None, Form()] = None,
    unit_price: Annotated[int | None, Form()] = None,
    amount: Annotated[int | None, Form()] = None,
    net_amount: Annotated[int, Form()] = 0,
    tax_category: Annotated[str | None, Form()] = None,
    tax_amount: Annotated[int | None, Form()] = None,
    total_amount: Annotated[int | None, Form()] = None,
    total_chinese: Annotated[str | None, Form()] = None,
    remarks: Annotated[str | None, Form()] = None,
    seller_name: Annotated[str | None, Form()] = None,
    seller_tax_id: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """步驟 3：核對修正 14 項發票資料、清除暫存影像、正式寫入發票清單並進入步驟 4（系統自動檢核）。"""
    invoice = _find_flow_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")
    _check_invoice_access(request, invoice, require_owner=True)
    _ensure_invoice_14_fields(invoice)

    if format_identification is not None:
        invoice["format_identification"] = format_identification.strip() or "三聯式手開發票"
    invoice["invoice_number"] = invoice_number.strip()
    invoice["invoice_date"] = invoice_date.strip()
    if buyer_name is not None:
        invoice["buyer_name"] = buyer_name.strip()
    if buyer_tax_id is not None:
        invoice["buyer_tax_id"] = buyer_tax_id.strip()
    if item_description is not None:
        invoice["item_description"] = item_description.strip()
        invoice["purpose"] = invoice["item_description"]
    if seller_name is not None:
        invoice["seller_name"] = seller_name.strip()
    if seller_tax_id is not None:
        invoice["seller_tax_id"] = seller_tax_id.strip()
    if remarks is not None:
        invoice["remarks"] = remarks.strip()

    cleaned_net = max(0, net_amount)
    cleaned_qty = max(1, quantity) if quantity is not None else int(invoice.get("quantity", 1))
    cleaned_unit = max(0, unit_price) if unit_price is not None else cleaned_net
    cleaned_amount = max(0, amount) if amount is not None else cleaned_net
    invoice["quantity"] = cleaned_qty
    invoice["unit_price"] = cleaned_unit
    invoice["amount"] = cleaned_amount
    invoice["net_amount"] = cleaned_net

    cat = (tax_category or invoice.get("tax_category", "應稅")).strip()
    if cat not in {"應稅", "零稅率", "免稅"}:
        cat = "應稅"
    invoice["tax_category"] = cat
    tax_type_map = {"應稅": "1 - 應稅", "零稅率": "2 - 零稅率", "免稅": "3 - 免稅"}
    invoice["tax_type"] = tax_type_map[cat]

    if tax_amount is not None:
        invoice["tax_amount"] = max(0, tax_amount) if cat == "應稅" else 0
    else:
        invoice["tax_amount"] = round(invoice["net_amount"] * 0.05) if cat == "應稅" else 0

    computed_total = invoice["net_amount"] + invoice["tax_amount"]
    invoice["total_amount"] = (
        max(0, total_amount)
        if (total_amount is not None and total_amount == computed_total)
        else computed_total
    )
    invoice["total_chinese"] = (
        total_chinese.strip()
        if (total_chinese and total_chinese.strip() and total_amount == computed_total)
        else _invoice_chinese_amount(invoice["total_amount"])
    )
    _commit_draft_after_correction(invoice_id, invoice)
    return RedirectResponse(f"/invoices/{invoice_id}/flow?step=4", status_code=303)


@router.post("/{invoice_id}/flow/advance")
def advance_flow_step(
    request: Request,
    invoice_id: int,
    from_step: Annotated[int, Form()],
) -> RedirectResponse:
    """前往下一步驟；若尚未完成步驟 3 採購核對修正則不寫入發票清單。"""
    max_flow_step = getattr(request.state, "invoice_max_step", 9)
    if from_step >= max_flow_step and max_flow_step < 9:
        raise DeniedError
    invoice = _find_flow_invoice(invoice_id)
    if invoice is not None:
        _check_invoice_access(request, invoice, require_owner=(from_step <= 3))
    if invoice_id in _DRAFT_INVOICE_STORE and from_step == 1 and not invoice.get("file_name"):
        return RedirectResponse(f"/invoices/{invoice_id}/flow?step=1", status_code=303)

    next_step = min(max_flow_step, from_step + 1)
    if invoice:
        if from_step == 3:
            _commit_draft_after_correction(invoice_id, invoice)
        else:
            invoice["stage"] = max(invoice.get("stage", next_step), next_step)
    return RedirectResponse(f"/invoices/{invoice_id}/flow?step={next_step}", status_code=303)


@router.post("/{invoice_id}/flow/finish-first-four")
def finish_first_four_steps(request: Request, invoice_id: int) -> RedirectResponse:
    """非財務部門完成前四步發票核對，標記待財務部接續處理並返回發票管理列表。"""
    invoice = _find_flow_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")
    _check_invoice_access(request, invoice, require_owner=True)
    _commit_draft_after_correction(invoice_id, invoice)
    invoice["first_four_checked"] = True
    invoice["stage"] = max(int(invoice.get("stage", 4)), 4)
    return RedirectResponse("/invoices", status_code=303)


@router.post("/{invoice_id}/flow/complete")
def complete_invoice_flow(request: Request, invoice_id: int) -> RedirectResponse:
    """確認發票作業完成。"""
    if getattr(request.state, "invoice_max_step", 9) < 9:
        raise DeniedError
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")

    invoice["is_completed"] = True
    invoice["stage"] = 9
    return RedirectResponse("/invoices", status_code=303)


@router.post("/{invoice_id}/review")
def review_invoice(
    request: Request,
    invoice_id: int,
    decision: Annotated[str, Form()],
    purpose: Annotated[str | None, Form()] = None,
    account_code: Annotated[str | None, Form()] = None,
    deduction_code: Annotated[str | None, Form()] = None,
    tax_type: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """步驟 5：財務審核——核准、退回與更新會計欄位。"""
    if getattr(request.state, "invoice_max_step", 9) < 5:
        raise DeniedError
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")

    if purpose is not None:
        invoice["purpose"] = purpose
    if account_code is not None:
        invoice["account_code"] = account_code
    if deduction_code is not None:
        invoice["deduction_code"] = deduction_code
    if tax_type is not None:
        invoice["tax_type"] = tax_type

    if decision == "passed":
        invoice["review_status"] = "approved"
        invoice["status"] = "passed"
    else:
        invoice["review_status"] = "returned"
        invoice["status"] = "flagged"

    return RedirectResponse(f"/invoices/{invoice_id}/flow?step=5", status_code=303)


@router.post("/{invoice_id}/filing")
def generate_filing(request: Request, invoice_id: int) -> RedirectResponse:
    """步驟 6：產生示範申報檔。"""
    if getattr(request.state, "invoice_max_step", 9) < 6:
        raise DeniedError
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")

    inv_no = invoice["invoice_number"]
    buyer = invoice["buyer_tax_id"]
    net = str(invoice["net_amount"]).zfill(10)
    tax = str(invoice["tax_amount"]).zfill(10)
    invoice["filing_preview"] = f"21{buyer}{inv_no}{net}{tax}1"

    return RedirectResponse(f"/invoices/{invoice_id}/flow?step=6", status_code=303)


@router.post("/{invoice_id}/import-confirm")
def confirm_import(request: Request, invoice_id: int) -> RedirectResponse:
    """步驟 7：確認手動匯入國稅局軟體。"""
    if getattr(request.state, "invoice_max_step", 9) < 7:
        raise DeniedError
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")

    invoice["import_confirmed_at"] = datetime.now(UTC)
    return RedirectResponse(f"/invoices/{invoice_id}/flow?step=7", status_code=303)


@router.get("/{invoice_id}/export/{kind}")
def export_invoice_data(request: Request, invoice_id: int, kind: str) -> Response:
    """步驟 9：匯出結果檔案（申報檔 TXT、進項資料 CSV、傳票 CSV）。"""
    if getattr(request.state, "invoice_max_step", 9) < 9:
        raise DeniedError
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")

    inv_no = invoice["invoice_number"]

    if kind == "report":
        content = (
            invoice.get("filing_preview")
            or f"21{invoice['buyer_tax_id']}{inv_no}{str(invoice['net_amount']).zfill(10)}{str(invoice['tax_amount']).zfill(10)}1"
        )
        filename = f"{inv_no}_report.txt"
        media_type = "text/plain; charset=utf-8"
    elif kind == "input":
        content = (
            "發票號碼,開立日期,賣方統編,買方統編,未稅金額,營業稅額,含稅總額\n"
            f"{inv_no},{invoice['invoice_date']},{invoice['seller_tax_id']},{invoice['buyer_tax_id']},"
            f"{invoice['net_amount']},{invoice['tax_amount']},{invoice['total_amount']}\n"
        )
        filename = f"{inv_no}_input.csv"
        media_type = "text/csv; charset=utf-8"
    elif kind == "voucher":
        voucher = _build_voucher(invoice)
        rows = ["科目,借方金額,貸方金額"]
        for line in voucher["lines"]:
            rows.append(f"{line['account']},{line['debit']},{line['credit']}")
        rows.append(f"合計,{voucher['total_debit']},{voucher['total_credit']}")
        content = "\n".join(rows) + "\n"
        filename = f"{inv_no}_voucher.csv"
        media_type = "text/csv; charset=utf-8"
    else:
        raise HTTPException(status_code=400, detail="未知的匯出類型")

    encoded_fn = quote(filename)
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_fn}"},
    )
