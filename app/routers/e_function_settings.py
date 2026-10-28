"""E 功能字典／部門配置頁；核定本機初始化暫免登入，雲端關閉。"""

from dataclasses import asdict
from ipaddress import ip_address
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.constants import WELCOME_CODE
from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.templating import templates
from app.routers.e_admin import require_configuration_access, require_e_origin
from app.schemas.e_admin import ConflictError, EError, FeatureInput, UnavailableError
from app.schemas.e_function_settings import (
    FunctionListQuery,
    FunctionStatusInput,
)
from app.services import e_function_settings_service as service
from app.services.e_registry import REGISTRY


def require_local_settings(request: Request) -> None:
    """限定本機部署與可信來源；身分／權限由下一個相依另外驗證。"""
    if settings.APP_ENV != "local" or settings.is_vercel or settings.module_backend != "remote":
        raise NotFoundError
    try:
        peer = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        peer = False
    if not peer or request.url.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
        "invoiceguard-ai-admin.localhost",
    }:
        raise NotFoundError


router = APIRouter(
    tags=["06_E local settings"],
    dependencies=[Depends(require_local_settings), Depends(require_configuration_access)],
)


def _render(
    request: Request, name: str, values: dict[str, object], *, status_code: int = 200
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "pages/e_function_settings/" + name,
        {
            "registered_codes": {item.code for item in REGISTRY},
            "builtin_welcome_code": WELCOME_CODE,
            **values,
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )


@router.get("/functions", response_class=HTMLResponse)
def function_list(request: Request, saved: bool = False) -> HTMLResponse:
    """搜尋、平台／狀態篩選與分頁；內容編輯使用獨立頁。"""
    query = _query(dict(request.query_params))
    try:
        page = service.search_functions(query)
    except UnavailableError as error:
        return _render(
            request,
            "functions.html",
            {"query": query, "error": error.message, "retry_url": _list_url(query)},
            status_code=503,
        )
    query = page.query
    return _render(
        request,
        "functions.html",
        {
            "query": query,
            "items": page.items,
            "total": page.total,
            "pages": page.pages,
            "new_url": "/functions/new?" + urlencode(query.model_dump()),
            "edit_urls": {
                item.function_id: f"/functions/{item.function_id}/edit?"
                + urlencode(query.model_dump())
                for item in page.items
            },
            "previous_url": _list_url(query.model_copy(update={"page": max(1, query.page - 1)})),
            "next_url": _list_url(query.model_copy(update={"page": query.page + 1})),
            "saved": saved,
        },
    )


def _query(values: dict[str, object], *, posted: bool = False) -> FunctionListQuery:
    prefix = "return_" if posted else ""
    fields = {
        key: values.pop(prefix + key)
        for key in ("q", "platform", "status", "page")
        if prefix + key in values
    }
    try:
        return FunctionListQuery.model_validate(fields)
    except ValidationError:
        raise EError from None


def _list_url(query: FunctionListQuery, *, saved: bool = False) -> str:
    values = query.model_dump()
    if saved:
        values["saved"] = "true"
    return "/functions?" + urlencode(values)


def _detail(
    request: Request,
    function_id: UUID | None,
    values: dict[str, object],
    query: FunctionListQuery,
    revision: str = "",
    error: EError | None = None,
) -> HTMLResponse:
    safe_values = {
        "function_code": str(values.get("function_code", ""))[:100],
        "function_name": str(values.get("function_name", ""))[:200],
        "app_scope": values.get("app_scope", "admin"),
        "is_active": values.get("is_active") in (True, "true", "on", "1"),
    }
    blocked = function_id is not None and isinstance(error, (ConflictError, UnavailableError))
    return _render(
        request,
        "detail.html",
        {
            "function_id": function_id,
            "values": safe_values,
            "query": query,
            "revision": revision,
            "registry": REGISTRY,
            "back_url": _list_url(query),
            "reload_url": f"/functions/{function_id}/edit?" + urlencode(query.model_dump()),
            "error": error.message if error else "",
            "blocked": blocked,
        },
        status_code=error.status if error else 200,
    )


@router.get("/functions/new", response_class=HTMLResponse)
def function_new(request: Request) -> HTMLResponse:
    """新增預設停用，不建立 DB 資料或授權。"""
    return _detail(request, None, {}, _query(dict(request.query_params)))


@router.get("/functions/{function_id}/edit", response_class=HTMLResponse)
def function_edit(request: Request, function_id: UUID) -> HTMLResponse:
    """顯示既有功能與內容指紋；代碼不可修改。"""
    query = _query(dict(request.query_params))
    item = service.get_function(function_id)
    return _detail(
        request,
        function_id,
        asdict(item),
        query,
        service.content_revision(item),
    )


@router.post("/functions", dependencies=[Depends(require_e_origin)])
async def function_create(request: Request) -> HTMLResponse:
    """新增字典，不接受 Query String 指定更新目標。"""
    return await _function_save(request, None)


@router.post("/functions/{function_id}", dependencies=[Depends(require_e_origin)])
async def function_update(request: Request, function_id: UUID) -> HTMLResponse:
    """更新目標僅從已解析 UUID 路徑取得。"""
    return await _function_save(request, function_id)


async def _single_form(request: Request) -> dict[str, object]:
    form = await request.form()
    if any(len(form.getlist(key)) != 1 for key in form):
        raise EError
    return dict(form)


def _revision(data: dict[str, object], *, required: bool) -> str:
    revision = data.pop("expected_revision", "")
    if not isinstance(revision, str) or (
        (required or revision)
        and (len(revision) != 64 or any(char not in "0123456789abcdef" for char in revision))
    ):
        raise EError
    return revision


async def _function_save(request: Request, function_id: UUID | None) -> HTMLResponse:
    """嚴格欄位白名單與單值；失敗保留安全欄位，過期內容必須重新載入。"""
    data = await _single_form(request)
    query = _query(data, posted=True)
    revision = _revision(data, required=function_id is not None)
    data.setdefault("is_active", False)
    try:
        parsed = FeatureInput.model_validate(data)
    except ValidationError:
        return _detail(request, function_id, data, query, revision, EError())
    try:
        service.save_function(parsed, function_id, revision or None)
    except (ConflictError, UnavailableError) as error:
        return _detail(request, function_id, parsed.model_dump(), query, revision, error)
    except EError as error:
        if error.status != 400:
            raise
        return _detail(request, function_id, parsed.model_dump(), query, revision, error)
    return RedirectResponse(
        _list_url(query, saved=True), status_code=303, headers={"Cache-Control": "no-store"}
    )


@router.post("/functions/{function_id}/status", dependencies=[Depends(require_e_origin)])
async def function_status(request: Request, function_id: UUID) -> HTMLResponse:
    """停用／重新啟用只更新旗標，不接受代碼、名稱或授權欄位。"""
    data = await _single_form(request)
    query = _query(data, posted=True)
    revision = _revision(data, required=True)
    try:
        status = FunctionStatusInput.model_validate(data)
    except ValidationError:
        raise EError from None
    try:
        service.change_function_status(function_id, status.is_active, revision)
    except EError as error:
        if error.status not in {400, 409}:
            raise
        item = service.get_function(function_id)
        return _detail(request, function_id, asdict(item), query, revision, error)
    return RedirectResponse(
        _list_url(query, saved=True), status_code=303, headers={"Cache-Control": "no-store"}
    )


@router.get("/department-functions")
def department_functions(department_id: UUID | None = None) -> RedirectResponse:
    """舊書籤只導向部門內容頁，不再讀取或顯示矩陣。"""
    target = f"/department/{department_id}/edit" if department_id else "/department"
    return RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})


@router.post("/department-functions/{department_id}", dependencies=[Depends(require_e_origin)])
@router.post(
    "/department-functions/{department_id}/{stage}", dependencies=[Depends(require_e_origin)]
)
def retired_department_functions(department_id: UUID, stage: str = "save") -> HTMLResponse:
    """舊表單已停用，不能把 POST 轉送到新表單造成非預期修改。"""
    return HTMLResponse(
        "此設定入口已移除，請返回部門列表重新開啟內容頁。",
        status_code=410,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )
