"""
模組名稱：app.routers.users
功能說明：使用者管理——列表搜尋、啟用 / 停用帳號。
路由：GET /users
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.deps import require_admin
from app.core.templating import templates

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_admin)])


@router.get("", response_class=HTMLResponse)
def user_list(request: Request) -> HTMLResponse:
    """使用者列表。"""
    # TODO(JerryChien): user_service.list_users(db, keyword=..., page=...)
    # TODO(JerryChien): 新增 POST /users/{user_id}/status（加 Depends(verify_same_origin)，寫入 audit_logs）
    return templates.TemplateResponse(request, "pages/users/list.html")
