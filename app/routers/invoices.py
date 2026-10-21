"""
模組名稱：app.routers.invoices
功能說明：發票審核——列表、詳情、人工複核。
路由：GET /invoices、GET /invoices/{invoice_id}
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.deps import require_admin
from app.core.templating import templates

router = APIRouter(prefix="/invoices", tags=["invoices"], dependencies=[Depends(require_admin)])


@router.get("", response_class=HTMLResponse)
def invoice_list(request: Request) -> HTMLResponse:
    """發票審核列表，預設顯示待人工複核（flagged）的發票。"""
    # TODO(JerryChien): invoice_review_service.list_invoices(db, status=..., page=...)
    return templates.TemplateResponse(request, "pages/invoices/list.html")


@router.get("/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: int) -> HTMLResponse:
    """發票審核詳情：辨識結果、AI 判斷理由、核准 / 駁回。"""
    # TODO(JerryChien): invoice_review_service.get_invoice(db, invoice_id)
    # TODO(JerryChien): 新增 POST /invoices/{invoice_id}/review（加 Depends(verify_same_origin)，寫入 audit_logs）
    return templates.TemplateResponse(
        request, "pages/invoices/detail.html", {"invoice_id": invoice_id}
    )
