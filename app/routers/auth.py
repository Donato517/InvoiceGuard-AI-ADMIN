"""模組名稱：app.routers.auth；功能說明：ADMIN 網站 Session 登入／登出，依目前部門功能勾選授權。

路由：GET /login、POST /login、POST /logout。
注意事項：讀 v1.3 UUID 帳號與部門設定，登入只更新登入紀錄，不建立 DB Session。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.constants import SESSION_COOKIE_NAME, WELCOME_CODE, WELCOME_PATH
from app.core.config import settings
from app.core.constants import SITE_NAMES
from app.core.exceptions import NotFoundError
from app.core.security import clear_session_cookie
from app.core.templating import templates
from app.routers import e_admin as e_guard
from app.routers.e_admin import local_setup_available, require_e_origin, session_evidence
from app.schemas.e_admin import Action, EError, UnauthenticatedError
from app.schemas.e_login import LoginInput
from app.services.e_admin_service import EModule, get_e_module
from app.services.e_registry import REGISTRY, Capability, validate_registry

router = APIRouter(tags=["auth"])
Module = Annotated[EModule, Depends(get_e_module)]
LOGIN_TEMPLATE = "pages/auth/login.html"


def _next_path(value: str, registry: tuple[Capability, ...] = REGISTRY) -> str:
    validate_registry(registry)
    # 使用者核定：無論從哪個入口登入，第一站固定為歡迎頁。
    return WELCOME_PATH


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
            "local_setup_available": local_setup_available(request),
            "brand_name": SITE_NAMES["FRONTEND"].removesuffix(" AI"),
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    module: Module,
    next_path: Annotated[str, Query(alias="next")] = WELCOME_PATH,
) -> HTMLResponse:
    """顯示登入表單；只允許固定 E 入口作為登入後跳轉。"""
    return _render_login(request, module, next_path)


@router.post("/login", response_model=None)
async def login(request: Request, module: Module) -> HTMLResponse | RedirectResponse:
    """驗帳密並組合部門功能 Session；有效員工成功後一律到歡迎頁。"""
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
            WELCOME_PATH,
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
    """結束網站登入 Cookie，不額外改寫 DB Session 或權限資料。"""
    module.logout(session_evidence(request))
    response = RedirectResponse("/login", status_code=303, headers={"Cache-Control": "no-store"})
    clear_session_cookie(response)
    return response


@router.post("/switch-department", dependencies=[Depends(require_e_origin)])
async def switch_department(request: Request, module: Module) -> RedirectResponse:
    """切換頁首目前部門，同步更新主要部門與網站 Session。"""
    form = await request.form()
    raw_dept = form.get("switch_department_id", "")
    raw_next = form.get("next", WELCOME_PATH)
    try:
        department_id = UUID(str(raw_dept))
    except ValueError:
        raise EError from None
    issued = module.switch_department(session_evidence(request), department_id)
    target = WELCOME_PATH
    if isinstance(raw_next, str) and raw_next.startswith("/") and not raw_next.startswith("//"):
        try:
            identity = module.identity(issued.token)
            allowed_routes = {
                entry.route for entry in module.sidebar_menu(identity) if entry.route
            } | {WELCOME_PATH, "/workspace", "/account"}
            for route in allowed_routes:
                if raw_next == route or raw_next.startswith(route + "/"):
                    if route in {"/workspace", "/account"}:
                        module.workspace_access(identity)
                    target = raw_next
                    break
        except EError:
            target = WELCOME_PATH
    response = RedirectResponse(
        target,
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


@router.get("/")
def admin_index() -> RedirectResponse:
    """網站入口固定先到歡迎頁，再由歡迎頁檢查身分。"""
    return RedirectResponse(WELCOME_PATH, status_code=303, headers={"Cache-Control": "no-store"})


@router.get(WELCOME_PATH, response_class=HTMLResponse)
def welcome(request: Request, module: Module) -> HTMLResponse:
    """本機維持設定例外；正式僅有效 ADMIN 可讀，不依賴部門手動授權。"""
    if local_setup_available(request):
        request.state.e_local_setup = True
    else:
        if settings.APP_ENV == "local" and not settings.is_vercel and e_guard.LOCAL_SETUP_ENABLED:
            raise NotFoundError
        identity = module.identity(session_evidence(request))
        module.check(identity, WELCOME_CODE, Action.VIEW)
        request.state.e_welcome_authenticated = True
        request.state.header_departments = module.departments(identity)
        request.state.header_selected_department_id = identity.selected_department_id
        # 選單屬可選投影；不能因無業務功能或 G 暫時不可用而封鎖唯讀問候。
        try:
            request.state.e_welcome_menu = module.sidebar_menu(identity)
        except EError:
            request.state.e_welcome_menu = ()
    return templates.TemplateResponse(
        request,
        "pages/e_function_settings/welcome.html",
        {},
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )
