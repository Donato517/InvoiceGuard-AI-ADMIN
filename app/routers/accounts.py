"""模組名稱：app.routers.accounts
功能說明：正式帳號管理列表及內容頁，管理者直接修改、刪除採停用。
路由：GET /accounts、GET/POST /accounts/new、GET/POST /accounts/{user_id}/edit、POST /accounts/{user_id}/disable、POST /accounts/{user_id}/departments
注意事項：本機匿名測試限定 loopback 與核定 Host，雲端在正式授權接線前拒絕存取。
"""

from ipaddress import ip_address
from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.security import verify_same_origin
from app.core.templating import templates
from app.services import account_management_service as service


def require_local_accounts(request: Request) -> None:
    """正式授權接線前只允許已核定的本機匿名測試，不信任轉送來源宣告。"""
    if settings.APP_ENV != "local" or settings.is_vercel or settings.module_backend != "remote":
        raise NotFoundError
    try:
        peer_is_local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        peer_is_local = False
    if not peer_is_local or request.url.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
        "invoiceguard-ai-admin.localhost",
    }:
        raise NotFoundError


def require_account_origin(request: Request) -> None:
    """匿名寫入必須提供完整同源 Origin，不沿用缺漏來源的寬鬆規則。"""
    if request.headers.get("origin") != f"{request.url.scheme}://{request.headers.get('host')}":
        raise ForbiddenError("帳號操作必須從本機管理頁面送出")


router = APIRouter(
    prefix="/accounts", tags=["accounts"], dependencies=[Depends(require_local_accounts)]
)
WRITE_CHECKS = [Depends(verify_same_origin), Depends(require_account_origin)]
SAVED_MESSAGES = {
    "created": "已新增帳號",
    "updated": "已儲存帳號",
    "disabled": "已停用帳號，資料已保留",
    "departments": "已儲存帳號所屬部門與主要部門",
}


@router.get("", response_class=HTMLResponse)
def account_list(request: Request, q: str = "", page: int = 1, saved: str = "") -> HTMLResponse:
    """顯示可搜尋與分頁的正式帳號列表。"""
    return _list(request, q=q, page=page, message=SAVED_MESSAGES.get(saved, ""))


@router.get("/new", response_class=HTMLResponse)
def account_new(request: Request) -> HTMLResponse:
    """內容頁新增模式。"""
    return _detail(request)


@router.post("/new", dependencies=WRITE_CHECKS, response_model=None)
def account_create(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    department_ids: Annotated[list[UUID] | None, Form()] = None,
    primary_department_id: Annotated[UUID | None, Form()] = None,
) -> HTMLResponse | RedirectResponse:
    """建立帳號，成功後導向內容頁，重新整理不重複送出。"""
    try:
        account = service.create_account(
            email,
            password,
            department_ids=department_ids or [],
            primary_department_id=primary_department_id,
        )
    except service.AccountManagementError as error:
        return _detail(
            request,
            email=email,
            error=str(error),
            status_code=400,
            selected_ids=department_ids or [],
            primary_id=primary_department_id,
        )
    return RedirectResponse(f"/accounts/{account.user_id}/edit?saved=created", status_code=303)


@router.get("/{user_id}/edit", response_class=HTMLResponse)
def account_edit(request: Request, user_id: UUID, saved: str = "") -> HTMLResponse:
    """內容頁修改模式。"""
    return _detail(request, user_id=user_id, message=SAVED_MESSAGES.get(saved, ""))


@router.post("/{user_id}/edit", dependencies=WRITE_CHECKS, response_model=None)
def account_update(
    request: Request,
    user_id: UUID,
    email: Annotated[str, Form()],
    new_password: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """直接修改 Email 或重設密碼，不要求目標帳號原密碼。"""
    try:
        service.update_account(user_id, email, new_password)
    except service.AccountManagementError as error:
        return _detail(request, user_id=user_id, email=email, error=str(error), status_code=400)
    return RedirectResponse(f"/accounts/{user_id}/edit?saved=updated", status_code=303)


@router.post("/{user_id}/disable", dependencies=WRITE_CHECKS, response_model=None)
def account_disable(request: Request, user_id: UUID) -> HTMLResponse | RedirectResponse:
    """刪除採停用，不接受硬刪除，保留原有關聯。"""
    try:
        service.disable_account(user_id)
    except service.AccountManagementError as error:
        return _detail(request, user_id=user_id, error=str(error), status_code=400)
    return RedirectResponse("/accounts?saved=disabled", status_code=303)


@router.post("/{user_id}/departments", dependencies=WRITE_CHECKS, response_model=None)
def account_departments_update(
    request: Request,
    user_id: UUID,
    department_ids: Annotated[list[UUID] | None, Form()] = None,
    primary_department_id: Annotated[UUID | None, Form()] = None,
) -> HTMLResponse | RedirectResponse:
    """獨立儲存帳號多部門，不順帶變更 Email、密碼或權限。"""
    try:
        service.save_department_settings(user_id, department_ids or [], primary_department_id)
    except service.AccountManagementError as error:
        return _detail(
            request,
            user_id=user_id,
            error=str(error),
            status_code=400,
            selected_ids=department_ids or [],
            primary_id=primary_department_id,
        )
    return RedirectResponse(f"/accounts/{user_id}/edit?saved=departments", status_code=303)


def _list(request: Request, *, q: str = "", page: int = 1, message: str = "") -> HTMLResponse:
    accounts, total, error = [], 0, ""
    status_code = 200
    try:
        accounts, total = service.list_accounts(q, page)
    except service.AccountManagementError as caught:
        error, status_code = str(caught), 400
    pages = max(1, (total + service.PAGE_SIZE - 1) // service.PAGE_SIZE)
    return templates.TemplateResponse(
        request,
        "pages/accounts/list.html",
        {
            "accounts": accounts,
            "q": q,
            "page": page,
            "pages": pages,
            "total": total,
            "message": message,
            "error": error,
            "previous_url": "/accounts?" + urlencode({"q": q, "page": page - 1}),
            "next_url": "/accounts?" + urlencode({"q": q, "page": page + 1}),
        },
        status_code=status_code,
    )


def _detail(
    request: Request,
    *,
    user_id: UUID | None = None,
    email: str | None = None,
    message: str = "",
    error: str = "",
    status_code: int = 200,
    selected_ids: list[UUID] | None = None,
    primary_id: UUID | None = None,
) -> HTMLResponse:
    account = None
    if user_id is not None:
        try:
            account = service.get_account(user_id)
        except service.AccountManagementError as caught:
            error = error or str(caught)
            status_code = 404 if "找不到" in str(caught) else 503
    department_settings = None
    department_error = ""
    if user_id is None or account is not None:
        try:
            department_settings = service.get_department_settings(user_id)
        except service.AccountManagementError as caught:
            department_error = str(caught)
    if selected_ids is not None and department_settings is not None:
        department_settings = department_settings.model_copy(
            update={"selected_ids": selected_ids, "primary_department_id": primary_id}
        )
    response = templates.TemplateResponse(
        request,
        "pages/accounts/detail.html",
        {
            "account": account,
            "user_id": user_id,
            "email": email if email is not None else (account.email if account else ""),
            "message": message,
            "error": error,
            "department_settings": department_settings,
            "department_error": department_error,
        },
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response
