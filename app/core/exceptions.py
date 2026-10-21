"""
模組名稱：app.core.exceptions
功能說明：後台例外類別與全域例外處理器——未登入導向登入頁、無權限顯示 403、錯誤頁。
注意事項：
    - 錯誤頁只顯示易懂的中文訊息，不顯示內部錯誤細節；完整堆疊寫入日誌。
"""

import logging
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.constants import SESSION_COOKIE_NAME
from app.core.templating import templates

logger = logging.getLogger(__name__)


class LoginRequiredError(Exception):
    """需要登入（未登入、登入過期或帳號已停用）；由全域處理器導向登入頁。"""


class ForbiddenError(Exception):
    """已登入但沒有權限，或請求來源檢查失敗。"""


class NotFoundError(Exception):
    """查詢的資料不存在。"""


def _login_redirect(request: Request) -> RedirectResponse:
    """導向登入頁並清除登入 cookie，記錄原本要去的頁面（登入後導回）。"""
    next_path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    response = RedirectResponse(f"/login?{urlencode({'next': next_path})}", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


def _error_page(request: Request, status_code: int, message: str) -> HTMLResponse:
    """顯示通用錯誤頁。"""
    return templates.TemplateResponse(
        request,
        "pages/errors/error.html",
        {"status_code": status_code, "message": message},
        status_code=status_code,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """在 FastAPI app 上註冊全域例外處理器（於 app.main 呼叫）。"""

    @app.exception_handler(LoginRequiredError)
    async def handle_login_required(request: Request, exc: LoginRequiredError) -> RedirectResponse:
        return _login_redirect(request)

    @app.exception_handler(ForbiddenError)
    async def handle_forbidden(request: Request, exc: ForbiddenError) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "pages/errors/403.html",
            {"message": str(exc) or "沒有權限執行此操作"},
            status_code=403,
        )

    @app.exception_handler(NotFoundError)
    async def handle_not_found(request: Request, exc: NotFoundError) -> HTMLResponse:
        return templates.TemplateResponse(request, "pages/errors/404.html", status_code=404)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
        if exc.status_code == 404:
            return templates.TemplateResponse(request, "pages/errors/404.html", status_code=404)
        return _error_page(request, exc.status_code, "無法處理這個請求")

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> HTMLResponse:
        logger.exception("未預期錯誤：%s %s", request.method, request.url.path)
        return _error_page(request, 500, "系統發生錯誤，請稍後再試")
