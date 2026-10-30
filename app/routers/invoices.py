"""
模組名稱：app.routers.invoices
功能說明：發票審核——列表、詳情、人工複核、新增發票與 9 步驟發票作業流程。
路由：/invoices
注意事項：
    - 本路由需管理者權限（require_admin）。
    - 步驟 1～9：上傳發票、AI OCR 辨識、資料核對、系統自動檢核、財務審核、產生申報檔、匯入確認、自動產生傳票、匯出結果。
    - 步驟 3 在財務退回時顯示「退回補正」區，逐項回覆後由 procurement_case_service（2-5）檢查再重送。
    - 步驟 6 與申報資料頁的檢核、81 字元申報資料與批次產檔由 format21_service（F09）負責；
      發票資料目前仍為示範資料，經 _to_filing_snapshot() 轉為 Schema v1.5 欄位後交給 F09。
"""

import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.constants import INVOICE_FLOW_STEPS, INVOICE_STATUS_LABEL
from app.core.deps import require_admin
from app.core.templating import templates
from app.routers.e_admin import session_evidence
from app.schemas.e_admin import Action, DeniedError, Target, UnauthenticatedError
from app.schemas.finance_review import (
    ACCOUNT_CODES,
    DEDUCTION_CODES,
    REVIEWABLE_STATUSES,
    STATUS_APPROVED,
    STATUS_LABELS,
    STATUS_REJECTED,
    STATUS_RETURNED,
    STATUS_SUBMITTED,
    TAX_TYPES,
    ReturnItem,
    ReviewCase,
    StatusChange,
)
from app.schemas.procurement_case import (
    ATTACHMENT_FIELD,
    RESPONSE_ACTIONS,
    CaseScope,
    CorrectionResponse,
    ResubmitPlan,
    ReturnRequest,
)
from app.services.company_profile_service import HardcodedCompanyProfileProvider
from app.services.e_admin_service import INVOICES, EModule, get_e_module
from app.services.finance_review_service import (
    FinanceReviewError,
    account_deduction_code,
    acknowledge_warning,
    approval_blockers,
    approve,
    build_change_history,
    build_review_case,
    check_account_deduction,
    check_field,
    field_label,
    reject,
    return_to_applicant,
    update_accounting_fields,
)
from app.services.format21_service import (
    build_format21_file,
    check_snapshot,
    format_record_line,
    summarize_401,
    validate_profile,
)
from app.services.procurement_case_service import (
    UNSPECIFIED_FIELD,
    ProcurementCaseError,
    latest_return,
    prepare_resubmit,
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

    # 待補正的案件排在最前面，提醒申請人優先處理（sort 為穩定排序，其餘維持原順序）
    invoices.sort(key=lambda i: i.get("review_status") != "returned")

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
    procurement_errors: list[str] | None = None,
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
            "finance_review": _finance_review_view(request, invoice)
            if step == 5 and _find_invoice(invoice_id)
            else None,
            "procurement_return": _procurement_return_view(invoice, procurement_errors)
            if step == 3
            else None,
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
        _note_attachment_upload(invoice_id, raw_bytes)
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
    if _returned_case(invoice) is not None:
        # 退回補正中：存檔後回到步驟 3，讓申請人逐項回覆並重新送出
        invoice["stage"] = 3
        return RedirectResponse(f"/invoices/{invoice_id}/flow?step=3", status_code=303)
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


# ---- 財務審核（2-4）----
# 示範發票沒有 AI 原值、檢核提醒與修改紀錄；以下依 C 的提交範例格式提供示範內容。
# TODO(StacieChen): 接上資料庫後改讀 invoice_field_reviews、invoices.validation_results、
#   invoice_changes、invoice_status_history，刪除此示範資料與 _FINANCE_CASES。
_DEMO_REVIEW_INPUTS: dict[int, dict[str, Any]] = {
    3: {
        "field_reviews": [
            {
                "field_scope": "header",
                "field_name": "total_amount",
                "ai_original_value": "1557",
                "submitted_value": "1575",
                "submission_round": 1,
            },
            {
                "field_scope": "header",
                "field_name": "seller_name",
                "ai_original_value": "範例銷貨",
                "submitted_value": "範例銷貨人",
                "submission_round": 1,
            },
        ],
        "warnings": [
            {
                "field": "seller_tax_id",
                "code": "tax_id_invalid",
                "message": "賣方統編檢查碼不符，請確認",
            }
        ],
        "changes": [
            {
                "submission_round": 1,
                "field_name": "total_amount",
                "old_value": "1557",
                "new_value": "1755",
            },
            {
                "submission_round": 1,
                "field_name": "seller_name",
                "old_value": "範例銷貨",
                "new_value": "範例銷貨人",
            },
            {
                "submission_round": 1,
                "field_name": "total_amount",
                "old_value": "1755",
                "new_value": "1575",
            },
        ],
    },
    4: {
        "field_reviews": [
            {
                "field_scope": "header",
                "field_name": "sales_amount",
                "ai_original_value": "1200",
                "submitted_value": "12000",
                "submission_round": 1,
            }
        ],
        "warnings": [],
        "changes": [
            {
                "submission_round": 1,
                "field_name": "sales_amount",
                "old_value": "1200",
                "new_value": "12000",
            }
        ],
    },
}
_FINANCE_CASES: dict[int, ReviewCase] = {}
_FINANCE_HISTORY: dict[int, list[StatusChange]] = {}
_REVIEW_STATUS_TO_V15 = {
    "pending": "pending_department_approval",
    "approved": STATUS_APPROVED,
    "returned": STATUS_RETURNED,
    "rejected": STATUS_REJECTED,
}
_TAX_TYPE_TO_CATEGORY = {"taxable": "應稅", "zero_rated": "零稅率", "tax_exempt": "免稅"}
_RETURN_EXTRA_FIELDS = ["attachment", "invoice_date", "seller_tax_id", "buyer_tax_id", "其他"]


def _finance_case(invoice: dict[str, Any]) -> ReviewCase:
    """取得示範發票的審核資料；第一次讀取時由示範發票轉為 Schema v1.5 欄位。"""
    invoice_id = int(invoice["id"])
    if invoice_id not in _FINANCE_CASES:
        demo = _DEMO_REVIEW_INPUTS.get(invoice_id, {})
        number = str(invoice.get("invoice_number") or "").strip().upper()
        deduction = str(invoice.get("deduction_code") or "").strip()[:1]
        category = invoice.get("tax_category") or "應稅"
        tax_type = next(
            (code for code, name in _TAX_TYPE_TO_CATEGORY.items() if name == category), None
        )
        row = {
            "invoice_id": str(invoice_id),
            "status": _REVIEW_STATUS_TO_V15.get(
                str(invoice.get("review_status") or "pending"), STATUS_SUBMITTED
            ),
            "submission_round": 1,
            "submitted_by": str(invoice.get("uploaded_by", "1")),
            "invoice_track": number[:2],
            "invoice_number": number[2:],
            "seller_tax_id": invoice.get("seller_tax_id"),
            "sales_amount": invoice.get("net_amount"),
            "tax_amount": invoice.get("tax_amount"),
            "total_amount": invoice.get("total_amount"),
            "tax_type": tax_type,
            "deduction_code": deduction if deduction and deduction in DEDUCTION_CODES else None,
            "validation_results": {"warnings": demo.get("warnings", [])},
        }
        _FINANCE_CASES[invoice_id] = build_review_case(row, demo.get("field_reviews", []))
    return _FINANCE_CASES[invoice_id]


def _sync_invoice(invoice: dict[str, Any], case: ReviewCase) -> None:
    """把審核結果寫回示範發票，讓步驟流程與申報檔頁面看到相同狀態。"""
    _FINANCE_CASES[int(invoice["id"])] = case
    if case.tax_type:
        invoice["tax_category"] = _TAX_TYPE_TO_CATEGORY[case.tax_type]
        invoice["tax_type"] = (
            f"{TAX_TYPES[case.tax_type][0]} - {_TAX_TYPE_TO_CATEGORY[case.tax_type]}"
        )
    if case.deduction_code:
        code, _, name = DEDUCTION_CODES[case.deduction_code].partition(" ")
        invoice["deduction_code"] = f"{code} - {name}"
    if case.status == STATUS_APPROVED:
        invoice["review_status"], invoice["status"] = "approved", "passed"
    elif case.status == STATUS_RETURNED:
        invoice["review_status"], invoice["status"] = "returned", "flagged"
        invoice["stage"] = min(int(invoice.get("stage", 3)), 3)
    elif case.status == STATUS_REJECTED:
        invoice["review_status"], invoice["status"] = "rejected", "rejected"


def _apply_change(invoice: dict[str, Any], case: ReviewCase, change: StatusChange) -> None:
    """套用狀態異動：更新欄位確認列、狀態與歷程（示範資料；正式版由 repository 同交易寫入）。"""
    updates = {(r.field_name, r.item_id): r for r in change.field_review_updates}
    reviews = tuple(updates.get((r.field_name, r.item_id), r) for r in case.field_reviews)
    _FINANCE_HISTORY.setdefault(int(invoice["id"]), []).append(change)
    _sync_invoice(invoice, replace(case, status=change.new_status, field_reviews=reviews))


def _reviewer_id(request: Request) -> str:
    return str(getattr(request.state, "invoice_user_id", "1"))


def _finance_review_view(request: Request, invoice: dict[str, Any]) -> dict[str, Any]:
    """步驟 5 財務審核畫面所需資料。"""
    case = _finance_case(invoice)
    demo = _DEMO_REVIEW_INPUTS.get(int(invoice["id"]), {})
    reviewable = case.status in REVIEWABLE_STATUSES
    field_options = [
        (r.field_name, r.item_id or "", field_label(r.field_name)) for r in case.field_reviews
    ]
    field_options += [(name, "", field_label(name)) for name in _RETURN_EXTRA_FIELDS]
    return {
        "case": case,
        "status_label": STATUS_LABELS.get(case.status, case.status),
        "reviewable": reviewable,
        "blockers": approval_blockers(case, _reviewer_id(request)) if reviewable else [],
        "field_reviews": [
            {"review": r, "label": field_label(r.field_name)} for r in case.field_reviews
        ],
        "warnings": case.warnings,
        "trails": [
            {"trail": t, "label": field_label(t.field_name)}
            for t in build_change_history(demo.get("changes", []))
        ],
        "history": [_history_entry(c) for c in _FINANCE_HISTORY.get(int(invoice["id"]), [])],
        "tax_types": TAX_TYPES,
        "deduction_codes": DEDUCTION_CODES,
        "account_codes": ACCOUNT_CODES,
        "account_code": str(invoice.get("account_code") or "").split(" ", 1)[0],
        "return_field_options": field_options,
    }


def _labelled_line(line: str) -> str:
    """把「欄位：內容」的欄位名稱換成中文；沒有「：」的行原樣顯示。"""
    field, sep, text = line.partition("：")
    return f"{field_label(field.strip())}：{text.strip()}" if sep else line.strip()


def _history_entry(change: StatusChange) -> dict[str, Any]:
    """審核歷程的一列：退回列出補正項目，重送列出申請人的逐項回覆（欄位皆顯示中文）。"""
    label = STATUS_LABELS.get(change.new_status, change.new_status)
    if change.return_items:
        lines = [f"{field_label(i.field)}：{i.reason}" for i in change.return_items]
        return {"change": change, "label": label, "lines": lines, "note": None}
    if change.old_status == STATUS_RETURNED and change.new_status in REVIEWABLE_STATUSES:
        lines = [
            _labelled_line(line) for line in (change.reason or "").splitlines() if line.strip()
        ]
        return {"change": change, "label": f"申請人重新送出，{label}", "lines": lines, "note": None}
    return {"change": change, "label": label, "lines": [], "note": change.reason}


def _finance_review_target(request: Request, invoice_id: int) -> dict[str, Any]:
    if getattr(request.state, "invoice_max_step", 9) < 5:
        raise DeniedError
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")
    return invoice


def _review_redirect(invoice_id: int) -> RedirectResponse:
    return RedirectResponse(f"/invoices/{invoice_id}/flow?step=5", status_code=303)


def _review_error(
    request: Request, invoice: dict[str, Any], error: FinanceReviewError
) -> HTMLResponse:
    response = _render_flow_page(request, invoice, 5, error=str(error))
    response.status_code = 400
    return response


@router.post("/{invoice_id}/finance-review/fields", response_model=None)
def finance_check_field(
    request: Request,
    invoice_id: int,
    field_name: Annotated[str, Form()],
    item_id: Annotated[str, Form()] = "",
    comment: Annotated[str, Form()] = "",
) -> Response:
    """步驟 5：財務確認一個「AI 原值 → 送出值」不同的欄位。"""
    invoice = _finance_review_target(request, invoice_id)
    try:
        case = check_field(
            _finance_case(invoice),
            field_name,
            item_id or None,
            _reviewer_id(request),
            datetime.now(UTC),
            comment.strip() or None,
        )
    except FinanceReviewError as error:
        return _review_error(request, invoice, error)
    _sync_invoice(invoice, case)
    return _review_redirect(invoice_id)


@router.post("/{invoice_id}/finance-review/warnings", response_model=None)
def finance_acknowledge_warning(
    request: Request,
    invoice_id: int,
    code: Annotated[str, Form()],
    field: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
) -> Response:
    """步驟 5：財務確認一項檢核提醒（備註必填）。"""
    invoice = _finance_review_target(request, invoice_id)
    try:
        case = acknowledge_warning(
            _finance_case(invoice), code, field, _reviewer_id(request), datetime.now(UTC), note
        )
    except FinanceReviewError as error:
        return _review_error(request, invoice, error)
    _sync_invoice(invoice, case)
    return _review_redirect(invoice_id)


@router.post("/{invoice_id}/finance-review/accounting", response_model=None)
def finance_update_accounting(
    request: Request,
    invoice_id: int,
    tax_type: Annotated[str, Form()] = "",
    deduction_code: Annotated[str, Form()] = "",
    purpose: Annotated[str | None, Form()] = None,
    account_code: Annotated[str | None, Form()] = None,
) -> Response:
    """步驟 5：更新課稅別、會計科目、扣抵代號與採購用途。

    選了會計科目但沒選扣抵代號時，帶出該科目的扣抵代號；兩者不同類時擋下。
    """
    invoice = _finance_review_target(request, invoice_id)
    account = (account_code or "").strip()
    if account and not deduction_code:
        deduction_code = account_deduction_code(account) or ""
    try:
        check_account_deduction(account, deduction_code or None)
        case = update_accounting_fields(
            _finance_case(invoice), tax_type or None, deduction_code or None
        )
    except FinanceReviewError as error:
        return _review_error(request, invoice, error)
    if purpose is not None:
        invoice["purpose"] = purpose
    if account:
        invoice["account_code"] = f"{account} {ACCOUNT_CODES[account][0]}"
    _sync_invoice(invoice, case)
    return _review_redirect(invoice_id)


@router.post("/{invoice_id}/finance-review/approve", response_model=None)
def finance_approve(request: Request, invoice_id: int) -> Response:
    """步驟 5：核准；有任何未處理項目時列出原因並擋下。"""
    invoice = _finance_review_target(request, invoice_id)
    case = _finance_case(invoice)
    try:
        change = approve(case, _reviewer_id(request), datetime.now(UTC))
    except FinanceReviewError as error:
        return _review_error(request, invoice, error)
    _apply_change(invoice, case, change)
    return _review_redirect(invoice_id)


@router.post("/{invoice_id}/finance-review/return", response_model=None)
def finance_return(
    request: Request,
    invoice_id: int,
    return_field: Annotated[list[str] | None, Form()] = None,
    return_reason: Annotated[list[str] | None, Form()] = None,
) -> Response:
    """步驟 5：逐項退回申請人補正；空白列略過。

    欄位選項值為「欄位名稱|item_id」，明細欄位才有 item_id。
    """
    invoice = _finance_review_target(request, invoice_id)
    items = []
    for raw_field, reason in zip(return_field or [], return_reason or [], strict=False):
        if not raw_field.strip() and not reason.strip():
            continue
        field, _, item_id = raw_field.partition("|")
        items.append(ReturnItem(field, reason, item_id or None))
    case = _finance_case(invoice)
    try:
        change = return_to_applicant(case, _reviewer_id(request), items, datetime.now(UTC))
    except FinanceReviewError as error:
        return _review_error(request, invoice, error)
    _apply_change(invoice, case, change)
    _take_return_snapshot(invoice)
    return _review_redirect(invoice_id)


@router.post("/{invoice_id}/finance-review/reject", response_model=None)
def finance_reject(
    request: Request, invoice_id: int, reason: Annotated[str, Form()] = ""
) -> Response:
    """步驟 5：駁回（終止，不能再重送），原因必填。"""
    invoice = _finance_review_target(request, invoice_id)
    case = _finance_case(invoice)
    try:
        change = reject(case, _reviewer_id(request), reason, datetime.now(UTC))
    except FinanceReviewError as error:
        return _review_error(request, invoice, error)
    _apply_change(invoice, case, change)
    return _review_redirect(invoice_id)


# ---- 採購退回補正（2-5）----
# 退回時把示範發票的欄位拍一份快照，申請人存檔或重新上傳後與快照比對，得到實際修改的欄位。
# TODO(StacieChen): 接上資料庫後改由 invoice_changes（本輪修改）判斷 changed_fields，
#   部門改讀 invoices.department_id，刪除 _RETURN_SNAPSHOTS 與 _DEMO_DEPARTMENT_ID。
_RETURN_SNAPSHOTS: dict[int, dict[str, Any]] = {}
# 示範發票沒有部門欄位；未登入部門（測試替身）時使用此示範值，發票部門一律視為與目前部門相同
_DEMO_DEPARTMENT_ID = "demo"
# 示範發票欄位 → Schema v1.5 欄位；發票字軌號碼另拆成 invoice_track／invoice_number
_SNAPSHOT_FIELDS = {
    "net_amount": "sales_amount",
    "tax_amount": "tax_amount",
    "total_amount": "total_amount",
    "seller_name": "seller_name",
    "seller_tax_id": "seller_tax_id",
    "buyer_tax_id": "buyer_tax_id",
    "buyer_name": "buyer_name",
    "invoice_date": "invoice_date",
    "invoice_number": "invoice_number",
}


def _file_digest(raw: bytes | None) -> str | None:
    return hashlib.sha256(raw).hexdigest() if raw else None


def _returned_case(invoice: dict[str, Any]) -> ReviewCase | None:
    """示範發票目前是否在「退回補正」；只有已進入財務審核的發票才有案件資料。"""
    if not _find_invoice(int(invoice["id"])) or invoice.get("review_status") != "returned":
        return None
    case = _finance_case(invoice)
    return case if case.status == STATUS_RETURNED else None


def _take_return_snapshot(invoice: dict[str, Any]) -> None:
    """財務退回成功後，記下退回當下的欄位與附件，作為比對修改的基準。"""
    _RETURN_SNAPSHOTS[int(invoice["id"])] = {
        "fields": {name: invoice.get(name) for name in _SNAPSHOT_FIELDS},
        "file_digest": _file_digest(invoice.get("file_bytes")),
        "attachment_replaced": False,
    }


def _note_attachment_upload(invoice_id: int, raw: bytes) -> None:
    """退回補正期間重新上傳附件：步驟 3 存檔會清除暫存檔，所以上傳當下就記錄已更換。"""
    snapshot = _RETURN_SNAPSHOTS.get(invoice_id)
    if snapshot is not None and raw and _file_digest(raw) != snapshot["file_digest"]:
        snapshot["attachment_replaced"] = True


def _changed_fields(invoice: dict[str, Any]) -> set[tuple[str, str | None]]:
    """與退回快照比對，回傳實際修改的 (v1.5 欄位, item_id)；示範發票只有表頭欄位。"""
    snapshot = _RETURN_SNAPSHOTS.get(int(invoice["id"]))
    if snapshot is None:
        return set()
    changed: set[tuple[str, str | None]] = set()
    for name, v15_name in _SNAPSHOT_FIELDS.items():
        old = str(snapshot["fields"].get(name) or "").strip().upper()
        new = str(invoice.get(name) or "").strip().upper()
        if old == new:
            continue
        if name == "invoice_number":
            if old[:2] != new[:2]:
                changed.add(("invoice_track", None))
            if old[2:] != new[2:]:
                changed.add(("invoice_number", None))
        else:
            changed.add((v15_name, None))
    if snapshot["attachment_replaced"] or (
        invoice.get("file_bytes") and _file_digest(invoice["file_bytes"]) != snapshot["file_digest"]
    ):
        changed.add((ATTACHMENT_FIELD, None))
    # 「其他」沒有對應欄位：有任何欄位或附件修改時才算已補正
    if changed:
        changed.add((UNSPECIFIED_FIELD, None))
    return changed


def _return_request(invoice: dict[str, Any], case: ReviewCase) -> ReturnRequest | None:
    """把 2-4 的示範審核資料轉成 v1.5 的 invoice_status_history 與 v_invoice_return_fields 後讀取退回項目。"""
    history = [
        {
            "new_status": c.new_status,
            "changed_at": c.changed_at,
            "changed_by": c.changed_by,
            "reason": c.reason,
        }
        for c in _FINANCE_HISTORY.get(int(invoice["id"]), [])
    ]
    return_fields = [
        {"field_name": r.field_name, "item_id": r.item_id, "review_comment": r.review_comment}
        for r in case.field_reviews
        if not r.is_checked
    ]
    return latest_return(str(invoice["id"]), history, return_fields)


def _case_scope(request: Request) -> CaseScope:
    department = getattr(request.state, "header_selected_department_id", None)
    return CaseScope(_reviewer_id(request), str(department or _DEMO_DEPARTMENT_ID))


def _procurement_return_view(
    invoice: dict[str, Any], errors: list[str] | None
) -> dict[str, Any] | None:
    """步驟 3「退回補正」區所需資料；不在退回補正時為 None。"""
    case = _returned_case(invoice)
    if case is None:
        return None
    return_request = _return_request(invoice, case)
    changed = _changed_fields(invoice)
    items = [
        {
            "key": f"{item.field}|{item.item_id or ''}",
            "label": field_label(item.field),
            "reason": item.reason,
            "is_changed": item.key in changed,
        }
        for item in (return_request.items if return_request else ())
    ]
    return {
        "items": items,
        "actions": RESPONSE_ACTIONS,
        "errors": errors or [],
        "submission_round": case.submission_round,
        "returned_at": return_request.returned_at if return_request else None,
    }


def _v15_values(invoice: dict[str, Any]) -> dict[str, str]:
    """示範發票目前的值，以 v1.5 欄位名稱表示（重送時更新欄位確認列的送出值）。"""
    values = {v15: str(invoice.get(name) or "") for name, v15 in _SNAPSHOT_FIELDS.items()}
    number = str(invoice.get("invoice_number") or "").strip().upper()
    values["invoice_track"], values["invoice_number"] = number[:2], number[2:]
    return values


def _apply_resubmit(
    request: Request, invoice: dict[str, Any], case: ReviewCase, plan: ResubmitPlan
) -> None:
    """套用重送計畫：案件進入新一輪待財務審核（示範資料；正式版由 C 的 service 以 expected_status 為條件寫入）。"""
    values = _v15_values(invoice)
    reviews = tuple(
        replace(
            r,
            is_checked=False,
            checked_by=None,
            checked_at=None,
            review_comment=None,
            submitted_value=values.get(r.field_name, r.submitted_value)
            if r.item_id is None
            else r.submitted_value,
        )
        for r in case.field_reviews
    )
    _FINANCE_CASES[int(invoice["id"])] = replace(
        case,
        status=plan.target_status,
        submission_round=plan.next_round,
        field_reviews=reviews,
        invoice_track=values["invoice_track"],
        invoice_number=values["invoice_number"],
        seller_tax_id=values["seller_tax_id"],
        sales_amount=Decimal(str(invoice.get("net_amount") or 0)),
        tax_amount=Decimal(str(invoice.get("tax_amount") or 0)),
        total_amount=Decimal(str(invoice.get("total_amount") or 0)),
    )
    _FINANCE_HISTORY.setdefault(int(invoice["id"]), []).append(
        StatusChange(
            invoice_id=case.invoice_id,
            old_status=plan.expected_status,
            new_status=plan.target_status,
            reason=plan.reason,
            changed_by=_reviewer_id(request),
            changed_at=datetime.now(UTC),
        )
    )
    _RETURN_SNAPSHOTS.pop(int(invoice["id"]), None)
    invoice["review_status"] = "pending"
    invoice["first_four_checked"] = True
    invoice["stage"] = 4


@router.post("/{invoice_id}/procurement/resubmit", response_model=None)
def procurement_resubmit(
    request: Request,
    invoice_id: int,
    response_key: Annotated[list[str] | None, Form()] = None,
    response_action: Annotated[list[str] | None, Form()] = None,
    response_note: Annotated[list[str] | None, Form()] = None,
) -> Response:
    """步驟 3：逐項回覆退回項目（已補正／申覆）後重新送出財務審核。

    回覆選項值為「欄位名稱|item_id」；處理方式空白代表未回覆。檢查不通過時回 400 並列出所有原因。
    """
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")
    _check_invoice_access(request, invoice, require_owner=True)
    case = _finance_case(invoice)
    scope = _case_scope(request)
    notes = list(response_note or [])
    responses = []
    for index, (key, action) in enumerate(
        zip(response_key or [], response_action or [], strict=False)
    ):
        if not action.strip():
            continue
        field, _, item_id = key.partition("|")
        note = notes[index].strip() if index < len(notes) else ""
        responses.append(CorrectionResponse(field, action.strip(), note or None, item_id or None))
    # 示範發票沒有部門欄位，轉 v1.5 欄位時部門與目前範圍相同
    row = {
        "invoice_id": str(invoice_id),
        "status": case.status,
        "submission_round": case.submission_round,
        "department_id": scope.department_id,
    }
    return_request = _return_request(invoice, case) if case.status == STATUS_RETURNED else None
    if case.status == STATUS_RETURNED and return_request is None:
        raise HTTPException(status_code=400, detail="找不到退回紀錄，請聯絡財務人員")
    try:
        plan = prepare_resubmit(
            row,
            scope,
            return_request or ReturnRequest(str(invoice_id), (), None, None),
            responses,
            _changed_fields(invoice),
        )
    except ProcurementCaseError as error:
        messages = [blocker.message for blocker in error.blockers]
        response = _render_flow_page(
            request,
            invoice,
            3,
            error=None if return_request else str(error),
            procurement_errors=messages,
        )
        response.status_code = 400
        return response
    _apply_resubmit(request, invoice, case, plan)
    return RedirectResponse(f"/invoices/{invoice_id}/flow?step=4", status_code=303)


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
