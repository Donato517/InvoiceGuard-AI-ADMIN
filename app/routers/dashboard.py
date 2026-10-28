"""
模組名稱：app.routers.dashboard
功能說明：儀表板（後台首頁）。
路由：GET /dashboard；網站首頁由 auth 的內建歡迎入口處理
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.deps import require_admin
from app.core.templating import templates

router = APIRouter(tags=["dashboard"], dependencies=[Depends(require_admin)])


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """儀表板：今日上傳數、待複核數、高風險發票數、最近排程狀態。"""
    # TODO(JerryChien): 新增 dashboard_service.get_summary(db) 提供統計數字
    return templates.TemplateResponse(request, "pages/dashboard/index.html")
