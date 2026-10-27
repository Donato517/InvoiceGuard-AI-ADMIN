"""模組名稱：app.routers.auth；功能說明：ADMIN 登入／登出，接 B 的共用認證核心。

路由：GET /login、POST /login、POST /logout。
注意事項：不再以舊 BIGINT users 登入發行 E 憑證；B adapter 缺少時回 503。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import SecretStr

from app.constants import SESSION_COOKIE_NAME
from app.core.config import settings
from app.core.security import clear_session_cookie
from app.core.templating import templates
from app.routers.e_admin import require_e_origin, session_evidence
from app.services.e_admin_service import EModule, get_e_module
from app.services.e_registry import REGISTRY

router = APIRouter(tags=["auth"])
Module = Annotated[EModule, Depends(get_e_module)]
LOGIN_TEMPLATE = "pages/auth/login.html"


def _next_path(value: str) -> str:
    allowed = {"/workspace", "/account", *(item.route for item in REGISTRY)}
    return value if value in allowed else "/workspace"


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next_path: Annotated[str, Query(alias="next")] = "/workspace",
) -> HTMLResponse:
    """顯示登入表單；只允許固定 E 入口作為登入後跳轉。"""
    return templates.TemplateResponse(
        request,
        LOGIN_TEMPLATE,
        {"next": _next_path(next_path)},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/login", response_model=None)
def login(
    request: Request,
    module: Module,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_path: Annotated[str, Form(alias="next")] = "/workspace",
) -> RedirectResponse:
    """B 建立 Session，E 確認 UUID／管理站資格後才寫入 session-only Cookie。"""
    require_e_origin(request)
    issued = module.login(email, SecretStr(password))
    response = RedirectResponse(
        _next_path(next_path), status_code=303, headers={"Cache-Control": "no-store"}
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        issued.token.get_secret_value(),
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return response


@router.post("/logout", dependencies=[Depends(require_e_origin)])
def logout(request: Request, module: Module) -> RedirectResponse:
    """由 B 撤銷服務端 Session，完成後清除 Cookie；失敗不假稱已撤銷。"""
    module.logout(session_evidence(request))
    response = RedirectResponse("/login", status_code=303, headers={"Cache-Control": "no-store"})
    clear_session_cookie(response)
    return response
