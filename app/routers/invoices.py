"""
模組名稱：app.routers.invoices
功能說明：發票審核——列表、詳情、人工複核、新增發票與 9 步驟發票作業流程。
路由：/invoices
注意事項：
    - 本路由需管理者權限（require_admin）。
    - 步驟 1～9：上傳發票、AI OCR 辨識、資料核對、系統自動檢核、財務審核、產生申報檔、匯入確認、自動產生傳票、匯出結果。
"""

from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.constants import INVOICE_FLOW_STEPS, INVOICE_STATUS_LABEL
from app.core.deps import require_admin
from app.core.templating import templates

router = APIRouter(prefix="/invoices", tags=["invoices"], dependencies=[Depends(require_admin)])

# 示範發票資料庫（對應原型 seedInvoices）
_INVOICE_STORE: list[dict[str, Any]] = [
    {
        "id": 1,
        "invoice_number": "AB12345678",
        "invoice_date": "113/08/15",
        "seller_tax_id": "24681357",
        "buyer_tax_id": "12345678",
        "item_description": "辦公耗材與文具用品",
        "net_amount": 6000,
        "tax_amount": 300,
        "total_amount": 6300,
        "stage": 9,
        "status": "passed",
        "review_status": "approved",
        "is_completed": True,
        "purpose": "供行政與財務部門日常辦公使用",
        "account_code": "5601 辦公用品費",
        "deduction_code": "1 - 進貨及費用",
        "tax_type": "1 - 應稅",
        "import_confirmed_at": datetime(2026, 8, 16, 14, 0, tzinfo=UTC),
        "filing_preview": "2112345678AB12345678000000600000000003001",
    },
    {
        "id": 2,
        "invoice_number": "AB12345679",
        "invoice_date": "113/08/15",
        "seller_tax_id": "24681357",
        "buyer_tax_id": "12345678",
        "item_description": "影印紙與印表機碳粉",
        "net_amount": 2000,
        "tax_amount": 100,
        "total_amount": 2100,
        "stage": 9,
        "status": "passed",
        "review_status": "approved",
        "is_completed": True,
        "purpose": "資訊設備耗材補充",
        "account_code": "5601 辦公用品費",
        "deduction_code": "1 - 進貨及費用",
        "tax_type": "1 - 應稅",
        "import_confirmed_at": datetime(2026, 8, 16, 15, 30, tzinfo=UTC),
        "filing_preview": "2112345678AB12345679000000200000000001001",
    },
    {
        "id": 3,
        "invoice_number": "AB12345680",
        "invoice_date": "113/08/15",
        "seller_tax_id": "24681357",
        "buyer_tax_id": "12345678",
        "item_description": "茶水間咖啡豆與茶包耗材",
        "net_amount": 1500,
        "tax_amount": 75,
        "total_amount": 1575,
        "stage": 4,
        "status": "flagged",
        "review_status": "pending",
        "is_completed": False,
        "purpose": "供辦公室茶水間使用",
        "account_code": "5602 什支",
        "deduction_code": "1 - 進貨及費用",
        "tax_type": "1 - 應稅",
        "import_confirmed_at": None,
        "filing_preview": None,
    },
    {
        "id": 4,
        "invoice_number": "AB12345681",
        "invoice_date": "113/08/15",
        "seller_tax_id": "24681357",
        "buyer_tax_id": "12345678",
        "item_description": "高階人體工學辦公椅",
        "net_amount": 12000,
        "tax_amount": 600,
        "total_amount": 12600,
        "stage": 5,
        "status": "flagged",
        "review_status": "pending",
        "is_completed": False,
        "purpose": "研發人員辦公設備添購",
        "account_code": "1401 辦公設備",
        "deduction_code": "2 - 固定資產",
        "tax_type": "1 - 應稅",
        "import_confirmed_at": None,
        "filing_preview": None,
    },
    {
        "id": 5,
        "invoice_number": "AB12345682",
        "invoice_date": "113/08/15",
        "seller_tax_id": "24681357",
        "buyer_tax_id": "12345678",
        "item_description": "網路線材與 RJ45 接頭",
        "net_amount": 800,
        "tax_amount": 40,
        "total_amount": 840,
        "stage": 6,
        "status": "passed",
        "review_status": "approved",
        "is_completed": False,
        "purpose": "機房線路整修備品",
        "account_code": "5605 修繕費",
        "deduction_code": "1 - 進貨及費用",
        "tax_type": "1 - 應稅",
        "import_confirmed_at": None,
        "filing_preview": "2112345678AB12345682000000080000000000401",
    },
]


def _find_invoice(invoice_id: int) -> dict[str, Any] | None:
    return next((i for i in _INVOICE_STORE if i["id"] == invoice_id), None)


def _build_flow_steps(
    current_step: int, invoice_id: int, is_completed: bool
) -> list[dict[str, Any]]:
    """建立發票作業 9 個步驟的中繼資料。"""
    steps = []
    for idx, label in enumerate(INVOICE_FLOW_STEPS, start=1):
        if is_completed:
            state = "active" if idx == current_step else "done"
        elif idx < current_step:
            state = "done"
        elif idx == current_step:
            state = "active"
        else:
            state = "locked"

        steps.append(
            {
                "number": idx,
                "label": label,
                "state": state,
                "url": f"/invoices/{invoice_id}/flow?step={idx}",
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


@router.get("", response_class=HTMLResponse)
def invoice_list(
    request: Request,
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """發票審核列表：分頁、狀態篩選、關鍵字搜尋。"""
    invoices = _INVOICE_STORE

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

    pending_count = sum(1 for i in _INVOICE_STORE if not i["is_completed"])
    passed_count = sum(1 for i in _INVOICE_STORE if i["status"] == "passed")
    flagged_count = sum(1 for i in _INVOICE_STORE if i["status"] == "flagged")

    counts = {
        "total": len(_INVOICE_STORE),
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
        },
    )


@router.get("/new")
def create_new_invoice() -> RedirectResponse:
    """建立新發票草稿並進入步驟 1（上傳發票）。"""
    new_id = max((int(i["id"]) for i in _INVOICE_STORE), default=0) + 1
    new_invoice: dict[str, Any] = {
        "id": new_id,
        "invoice_number": f"AB{12345677 + new_id}",
        "invoice_date": "113/08/15",
        "seller_tax_id": "24681357",
        "buyer_tax_id": "12345678",
        "item_description": "辦公用品",
        "net_amount": 6000,
        "tax_amount": 300,
        "total_amount": 6300,
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
    _INVOICE_STORE.insert(0, new_invoice)
    return RedirectResponse(f"/invoices/{new_id}/flow?step=1", status_code=303)


@router.get("/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: int) -> HTMLResponse:
    """發票審核詳情：辨識結果、AI 判斷理由、核准 / 駁回。"""
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")

    return templates.TemplateResponse(
        request,
        "pages/invoices/detail.html",
        {
            "invoice_id": invoice_id,
            "invoice": invoice,
        },
    )


@router.get("/{invoice_id}/flow", response_class=HTMLResponse)
def invoice_flow(
    request: Request,
    invoice_id: int,
    step: Annotated[int, Query(ge=1, le=9)] = 4,
) -> HTMLResponse:
    """發票作業流程（步驟 1～9）。"""
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")

    steps = _build_flow_steps(step, invoice_id, invoice["is_completed"])
    checks = _build_checks(invoice)
    voucher = _build_voucher(invoice)

    filing = None
    if invoice.get("filing_preview"):
        filing = {
            "file_name": f"{invoice['invoice_number']}.txt",
            "preview_text": invoice["filing_preview"],
        }

    return templates.TemplateResponse(
        request,
        "pages/invoices/flow.html",
        {
            "invoice": invoice,
            "step": step,
            "steps": steps,
            "checks": checks,
            "voucher": voucher,
            "filing": filing,
            "error": None,
        },
    )


@router.post("/{invoice_id}/flow/upload")
async def upload_invoice_file(
    invoice_id: int,
    file: Annotated[UploadFile | None, File()] = None,
) -> RedirectResponse:
    """步驟 1：上傳發票檔案並進入步驟 2（OCR 辨識預覽）。"""
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")

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
    invoice["stage"] = max(invoice.get("stage", 1), 2)
    return RedirectResponse(f"/invoices/{invoice_id}/flow?step=2", status_code=303)


@router.get("/{invoice_id}/file")
def get_invoice_file(invoice_id: int) -> Response:
    """取得已上傳發票檔案預覽內容。"""
    invoice = _find_invoice(invoice_id)
    if not invoice or not invoice.get("file_bytes"):
        raise HTTPException(status_code=404, detail="找不到發票檔案")
    return Response(
        content=invoice["file_bytes"],
        media_type=invoice.get("file_media_type", "image/jpeg"),
    )


@router.post("/{invoice_id}/flow/correct")
def correct_invoice_fields(
    invoice_id: int,
    invoice_number: Annotated[str, Form()],
    invoice_date: Annotated[str, Form()],
    seller_tax_id: Annotated[str | None, Form()] = None,
    buyer_tax_id: Annotated[str | None, Form()] = None,
    item_description: Annotated[str | None, Form()] = None,
    net_amount: Annotated[int, Form()] = 0,
) -> RedirectResponse:
    """步驟 3：核對修正發票資料並進入步驟 4（系統自動檢核）。"""
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")

    invoice["invoice_number"] = invoice_number.strip()
    invoice["invoice_date"] = invoice_date.strip()
    invoice["seller_tax_id"] = (seller_tax_id or "").strip()
    invoice["buyer_tax_id"] = (buyer_tax_id or "").strip()
    if item_description is not None:
        invoice["item_description"] = item_description.strip()
    invoice["net_amount"] = max(0, net_amount)
    invoice["tax_amount"] = round(invoice["net_amount"] * 0.05)
    invoice["total_amount"] = invoice["net_amount"] + invoice["tax_amount"]
    invoice["stage"] = max(invoice.get("stage", 3), 4)
    return RedirectResponse(f"/invoices/{invoice_id}/flow?step=4", status_code=303)


@router.post("/{invoice_id}/flow/advance")
def advance_flow_step(
    invoice_id: int,
    from_step: Annotated[int, Form()],
) -> RedirectResponse:
    """前往下一步驟。"""
    next_step = min(9, from_step + 1)
    invoice = _find_invoice(invoice_id)
    if invoice:
        invoice["stage"] = max(invoice.get("stage", 4), next_step)
    return RedirectResponse(f"/invoices/{invoice_id}/flow?step={next_step}", status_code=303)


@router.post("/{invoice_id}/flow/complete")
def complete_invoice_flow(invoice_id: int) -> RedirectResponse:
    """確認發票作業完成。"""
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")

    invoice["is_completed"] = True
    invoice["stage"] = 9
    return RedirectResponse("/invoices", status_code=303)


@router.post("/{invoice_id}/review")
def review_invoice(
    invoice_id: int,
    decision: Annotated[str, Form()],
    purpose: Annotated[str | None, Form()] = None,
    account_code: Annotated[str | None, Form()] = None,
    deduction_code: Annotated[str | None, Form()] = None,
    tax_type: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    """步驟 5：財務審核——核准、退回與更新會計欄位。"""
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
def generate_filing(invoice_id: int) -> RedirectResponse:
    """步驟 6：產生示範申報檔。"""
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
def confirm_import(invoice_id: int) -> RedirectResponse:
    """步驟 7：確認手動匯入國稅局軟體。"""
    invoice = _find_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="找不到指定發票")

    invoice["import_confirmed_at"] = datetime.now(UTC)
    return RedirectResponse(f"/invoices/{invoice_id}/flow?step=7", status_code=303)


@router.get("/{invoice_id}/export/{kind}")
def export_invoice_data(invoice_id: int, kind: str) -> Response:
    """步驟 9：匯出結果檔案（申報檔 TXT、進項資料 CSV、傳票 CSV）。"""
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
