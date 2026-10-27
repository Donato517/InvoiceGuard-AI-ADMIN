"""模組名稱：app.routers.auth；功能說明：ADMIN 登入／登出，接 B 的共用認證核心。

路由：GET /login、POST /login、POST /logout。
注意事項：不再以舊 BIGINT users 登入發行 E 憑證；B adapter 缺少時回 503。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.constants import SESSION_COOKIE_NAME
from app.core.config import settings
from app.core.constants import SITE_NAMES
from app.core.security import clear_session_cookie
from app.core.templating import templates
from app.routers.e_admin import require_e_origin, session_evidence
from app.schemas.e_admin import EError, UnauthenticatedError
from app.schemas.e_login import LoginInput
from app.services.e_admin_service import EModule, get_e_module
from app.services.e_registry import REGISTRY, Capability, validate_registry

router = APIRouter(tags=["auth"])
Module = Annotated[EModule, Depends(get_e_module)]
LOGIN_TEMPLATE = "pages/auth/login.html"


def _next_path(value: str, registry: tuple[Capability, ...] = REGISTRY) -> str:
    validate_registry(registry)
    allowed = {
        "/workspace",
        "/account",
        "/accounts",
        "/accounts/new",
        "/department",
        "/functions",
        "/functions/new",
        "/department-functions",
        *(item.route for item in registry if "{" not in item.route),
    }
    return value if value in allowed else "/workspace"


def _render_login(
    request: Request,
    module: EModule,
    next_path: str,
    *,
    email: str = "",
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        LOGIN_TEMPLATE,
        {
            "next": _next_path(next_path, module.registry),
            "email": email[:320],
            "error": error,
            "brand_name": SITE_NAMES["FRONTEND"].removesuffix(" AI"),
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    module: Module,
    next_path: Annotated[str, Query(alias="next")] = "/workspace",
) -> HTMLResponse:
    """顯示登入表單；只允許固定 E 入口作為登入後跳轉。"""
    return _render_login(request, module, next_path)


@router.post("/login", response_model=None)
async def login(request: Request, module: Module) -> HTMLResponse | RedirectResponse:
    """B 建立 Session，E 確認 UUID／管理站資格後才寫入 session-only Cookie。"""
    require_e_origin(request)
    form = await request.form()
    if any(len(form.getlist(key)) != 1 for key in form):
        raise EError
    try:
        data = LoginInput.model_validate(dict(form))
    except ValidationError:
        email = form.get("email", "")
        return _render_login(
            request,
            module,
            "/workspace",
            email=email if isinstance(email, str) else "",
            error="請確認 Email、密碼與表單內容。",
            status_code=400,
        )
    try:
        issued = module.login(data.email, data.password)
    except EError as error:
        return _render_login(
            request,
            module,
            data.next_path,
            email=data.email,
            error="帳號或密碼不正確，請重新輸入。"
            if isinstance(error, UnauthenticatedError)
            else error.message,
            status_code=error.status,
        )
    response = RedirectResponse(
        _next_path(data.next_path, module.registry),
        status_code=303,
        headers={"Cache-Control": "no-store"},
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
