"""
模組名稱：app.routers.dev_accounts
功能說明：開發者帳號——查詢、新增、修改（invoiceguard_identity.user_accounts），示範既有分層與本機 → 雲端串接。
路由：
    本機頁面（MODULE_BACKEND=remote）：GET /dev/accounts、POST /dev/accounts、POST /dev/accounts/{user_id}
    雲端 API（MODULE_BACKEND=cloud）：GET /api/dev/accounts、POST /api/dev/accounts、
        PATCH /api/dev/accounts/{user_id}
注意事項：
    - 讓開發者自行建立與維護自己的帳密，供後續登入功能串接；不是完整帳號管理。
    - 本機頁面經 dev_account_service 以既有 DB 模組直接讀寫雲端 PostgreSQL
      （DATABASE_URL 由 START.cmd 經 Vercel CLI 注入本機 ADMIN 程序）。
    - 本機頁面與雲端 API 都由 user_service → user_account_repository 讀寫；
      修改需驗證目前密碼。不提供刪除，不接受任意 SQL。
    - 本機頁面的表單由 HTMX 送出：POST 直接回傳更新後的頁面（不轉址），結果訊息不經網址參數；
      頁面只替換 #dev-accounts 區塊，不換頁。
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.deps import DbSession, require_cloud_db, require_dev_pages
from app.core.exceptions import NotFoundError
from app.core.security import verify_same_origin
from app.core.templating import templates
from app.schemas.account import AccountCreate, AccountSummary, AccountUpdate
from app.services import dev_account_service, user_service

PAGE_TEMPLATE = "pages/dev_accounts/index.html"
SAVED_MESSAGES = {"created": "已新增帳號", "updated": "已更新帳號"}

page = APIRouter(prefix="/dev/accounts", tags=["dev"], dependencies=[Depends(require_dev_pages)])
api = APIRouter(prefix="/api/dev/accounts", tags=["dev"], dependencies=[Depends(require_cloud_db)])


# ---- 本機頁面 ----


@page.get("", response_class=HTMLResponse)
def dev_account_page(request: Request, q: str = "", edit: UUID | None = None) -> HTMLResponse:
    """帳號列表／查詢、新增表單與修改表單；每次都重新向雲端讀取。"""
    return _render(request, q=q, edit=edit)


@page.post("", dependencies=[Depends(verify_same_origin)], response_class=HTMLResponse)
def dev_account_create(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> HTMLResponse:
    """新增帳號；成功時回傳重新讀取的頁面與訊息，失敗時回填 email（不回填密碼）。"""
    try:
        account = dev_account_service.create(email=email, password=password)
    except dev_account_service.DevAccountError as error:
        return _render(request, create_email=email, create_error=str(error), status_code=400)
    return _render(request, edit=account.user_id, message=SAVED_MESSAGES["created"])


@page.post("/{user_id}", dependencies=[Depends(verify_same_origin)], response_class=HTMLResponse)
def dev_account_update(
    request: Request,
    user_id: UUID,
    email: Annotated[str, Form()],
    current_password: Annotated[str, Form()],
    new_password: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """修改自己的帳號：需目前密碼，新密碼留空表示不變更。成功時回傳重新讀取的頁面與訊息。"""
    try:
        dev_account_service.update(
            user_id, email=email, current_password=current_password, new_password=new_password
        )
    except dev_account_service.DevAccountError as error:
        return _render(
            request, edit=user_id, edit_email=email, edit_error=str(error), status_code=400
        )
    return _render(request, edit=user_id, message=SAVED_MESSAGES["updated"])


def _render(
    request: Request,
    *,
    q: str = "",
    edit: UUID | None = None,
    message: str | None = None,
    status_code: int = 200,
    **context: object,
) -> HTMLResponse:
    accounts: list[AccountSummary] = []
    selected: AccountSummary | None = None
    error = None
    try:
        accounts = dev_account_service.search(q)
        if edit is not None:
            found = dev_account_service.search(str(edit))
            selected = found[0] if found else None
    except dev_account_service.DevAccountError as caught:
        error = str(caught)
    values = {
        "q": q,
        "accounts": accounts,
        "edit": edit,
        "selected": selected,
        "message": message,
        "error": error,
    }
    return templates.TemplateResponse(
        request, PAGE_TEMPLATE, values | context, status_code=status_code
    )


# ---- 雲端 API ----


@api.get("")
def dev_account_list(db: DbSession, q: str = "") -> JSONResponse:
    """查詢帳號（UUID 或 Email；空白列出最近 50 筆），不回傳 password_hash。"""
    try:
        accounts = user_service.search_accounts(db, q)
    except user_service.AccountStoreError as error:
        return _error(503, error.code)
    return JSONResponse({"items": [account.model_dump(mode="json") for account in accounts]})


@api.post("", dependencies=[Depends(verify_same_origin)])
def dev_account_insert(db: DbSession, data: AccountCreate) -> JSONResponse:
    """新增 active 帳號；Email 重複回 409，輸入不符回 400。"""
    try:
        account = user_service.create_account(db, email=data.email, password=data.password)
    except user_service.AccountInputError as error:
        return _error(409 if error.code == "email_taken" else 400, error.code)
    except user_service.AccountStoreError as error:
        return _error(503, error.code)
    return JSONResponse({"account": account.model_dump(mode="json")}, status_code=201)


@api.patch("/{user_id}", dependencies=[Depends(verify_same_origin)])
def dev_account_patch(db: DbSession, user_id: UUID, data: AccountUpdate) -> JSONResponse:
    """修改自己的帳號；目前密碼錯誤回 400，不存在回 404，Email 重複回 409。"""
    try:
        account = user_service.update_account(
            db,
            user_id,
            email=data.email,
            current_password=data.current_password,
            new_password=data.new_password,
        )
    except NotFoundError:
        return _error(404, "not_found")
    except user_service.AccountInputError as error:
        return _error(409 if error.code == "email_taken" else 400, error.code)
    except user_service.AccountStoreError as error:
        return _error(503, error.code)
    return JSONResponse({"account": account.model_dump(mode="json")})


def _error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code}}, status_code=status_code)
