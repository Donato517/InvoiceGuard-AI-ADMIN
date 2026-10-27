"""
模組名稱：app.routers.auth
功能說明：後台登入 / 登出頁面。登入成功後寫入已簽署的登入 cookie。
路由：GET /login、POST /login、POST /logout
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import settings
from app.core.deps import DEV_ADMIN, DbSession
from app.core.security import (
    clear_session_cookie,
    safe_next_path,
    set_session_cookie,
    verify_same_origin,
)
from app.core.templating import templates
from app.services import auth_service

router = APIRouter(tags=["auth"])

LOGIN_TEMPLATE = "pages/auth/login.html"


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request, next_path: Annotated[str, Query(alias="next")] = "/dashboard"
) -> HTMLResponse:
    """顯示登入表單。next 為登入後要回到的頁面。"""
    is_local = settings.APP_ENV == "local" and not settings.is_vercel
    return templates.TemplateResponse(
        request,
        LOGIN_TEMPLATE,
        {
            "next": safe_next_path(next_path),
            "is_local_dev": is_local,
            "email": "admin@example.com" if is_local else "",
        },
    )


@router.post("/login", dependencies=[Depends(verify_same_origin)], response_model=None)
def login(
    request: Request,
    db: DbSession,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next_path: Annotated[str, Form(alias="next")] = "/dashboard",
) -> HTMLResponse | RedirectResponse:
    """送出登入表單：驗證管理者帳號密碼，成功後寫入 cookie 並導回 next 頁面。"""

    admin = None
    if db is not None:
        try:
            admin = auth_service.authenticate(db, email, password)
        except Exception:
            admin = None

    if admin is None and settings.APP_ENV == "local" and not settings.is_vercel:
        clean = email.strip().lower()
        if clean in {"admin@example.com", "admin@invoiceguard.local", "admin"} or clean.startswith(
            "admin"
        ):
            admin = DEV_ADMIN

    if admin is None:
        context = {
            "next": safe_next_path(next_path),
            "email": email,
            "error": "帳號或密碼錯誤",
            "is_local_dev": settings.APP_ENV == "local" and not settings.is_vercel,
        }
        return templates.TemplateResponse(request, LOGIN_TEMPLATE, context, status_code=400)

    response = RedirectResponse(safe_next_path(next_path), status_code=303)
    set_session_cookie(response, admin.id)
    return response


@router.post("/logout", dependencies=[Depends(verify_same_origin)])
def logout() -> RedirectResponse:
    """登出：清除登入 cookie 並回到登入頁。"""
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response
