"""
模組名稱：app.routers.dashboard
功能說明：儀表板（後台首頁）。
路由：GET /dashboard；網站首頁由 auth 的內建歡迎入口處理
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.deps import require_admin
from app.core.templating import templates
from app.routers.invoices import _DRAFT_INVOICE_STORE, _INVOICE_STORE

router = APIRouter(tags=["dashboard"], dependencies=[Depends(require_admin)])


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """儀表板：今日上傳數、待複核數、高風險發票數、最近待處理發票。"""
    _DRAFT_INVOICE_STORE.clear()
    pending = [i for i in _INVOICE_STORE if not i["is_completed"]]
    completed = [i for i in _INVOICE_STORE if i["is_completed"]]
    flagged = [i for i in _INVOICE_STORE if i["status"] == "flagged"]

    summary = {
        "today_uploaded": len(_INVOICE_STORE),
        "pending_review": len(pending),
        "high_risk": len(flagged),
        "completed": len(completed),
    }

    return templates.TemplateResponse(
        request,
        "pages/dashboard/index.html",
        {
            "summary": summary,
            "pending_invoices": pending,
            "completed_invoices": completed,
        },
    )
