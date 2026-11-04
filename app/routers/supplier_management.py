"""正式 ADMIN 供應商 CRUD；共用主檔、獨立四操作、無本機免登入例外。"""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.core.templating import templates
from app.routers.e_admin import require_e_origin, session_evidence
from app.schemas.e_admin import Action, DeniedError, Target, UnavailableError
from app.schemas.supplier import (
    CREDIT_RATINGS,
    PAYMENT_BASES,
    PAYMENT_METHODS,
    PAYMENT_TERMS,
    SupplierInput,
)
from app.services import supplier_management_service as service
from app.services.admin_login_service import DepartmentLoginAdapter
from app.services.e_admin_service import EModule, get_e_module
from app.services.e_configuration_security import (
    ConfigurationContext,
    enter_configuration,
    leave_configuration,
)

CODE = "suppliers"
Module = Annotated[EModule, Depends(get_e_module)]
DEFAULTS = {
    "country_code": "TW",
    "currency": "TWD",
    "payment_terms": "net",
    "payment_basis": "invoice",
    "payment_days": "30",
    "payment_method": "transfer",
    "credit_rating": "unassessed",
}
MESSAGES = {
    "created": "已新增供應商",
    "saved": "已儲存供應商",
    "enabled": "已啟用供應商",
    "disabled": "已停用供應商，資料仍保留",
}


async def require_supplier_access(request: Request, module: Module) -> AsyncIterator[None]:
    """每個固定路由依操作驗權，並將既有 Session 交給交易內重驗。"""
    action = {
        "supplier_list": Action.VIEW,
        "supplier_detail": Action.VIEW,
        "supplier_new": Action.CREATE,
        "supplier_create": Action.CREATE,
        "supplier_edit": Action.EDIT,
        "supplier_update": Action.EDIT,
        "supplier_status": Action.DELETE,
    }.get(request.scope["route"].name)
    if action is None or not isinstance(module.access, DepartmentLoginAdapter):
        raise UnavailableError
    identity = module.identity(session_evidence(request))
    target = Target(global_resource=True)
    module.check(identity, CODE, action, target)
    if request.method != "GET":
        require_e_origin(request)
    allowed = {}
    for option in Action:
        try:
            module.check(identity, CODE, option, target)
            allowed[option.value] = True
        except DeniedError:
            allowed[option.value] = False
    request.state.supplier_allowed = allowed
    request.state.supplier_identity = identity
    request.state.e_configuration_authenticated = True
    request.state.e_configuration_menu = module.sidebar_menu(identity)
    request.state.header_departments = module.departments(identity)
    request.state.header_selected_department_id = identity.selected_department_id
    token = enter_configuration(ConfigurationContext(module.access, identity, ((CODE, action),)))
    try:
        yield
    finally:
        leave_configuration(token)


router = APIRouter(
    prefix="/suppliers", tags=["suppliers"], dependencies=[Depends(require_supplier_access)]
)


def render(
    request: Request, template_name: str, status: int = 200, **context: object
) -> HTMLResponse:
    """正式版型與依權限呈現的操作；不連接模擬服務。"""
    return templates.TemplateResponse(
        request,
        "pages/supplier_management/" + template_name + ".html",
        {
            "ratings": {key: value.replace("模擬", "") for key, value in CREDIT_RATINGS.items()},
            "terms": PAYMENT_TERMS,
            "bases": PAYMENT_BASES,
            "methods": PAYMENT_METHODS,
            "can": request.state.supplier_allowed,
            **context,
        },
        status_code=status,
    )


def identifier(value: str) -> UUID:
    """非 UUID 路徑視為查無資料，不送入 SQL。"""
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(404, "找不到指定供應商") from None


async def checked_form(request: Request, allowed: set[str]) -> dict:
    """拒絕多值與未知欄位，不接受操作者或權限自報。"""
    form = await request.form(max_fields=25)
    if set(form) - allowed or any(len(form.getlist(key)) != 1 for key in form):
        raise HTTPException(400, "表單包含未知或重複欄位，請重新開啟畫面")
    return dict(form)


@router.get("", response_class=HTMLResponse)
def supplier_list(
    request: Request,
    q: Annotated[str, Query(max_length=200)] = "",
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    result: str = "",
) -> HTMLResponse:
    """真實主檔列表、搜尋及分頁。"""
    rows, total = service.list_suppliers(q.strip(), page)
    return render(
        request,
        "list",
        suppliers=rows,
        q=q,
        page=page,
        total=total,
        pages=max(1, (total + 19) // 20),
        message=MESSAGES.get(result, ""),
    )


@router.get("/new", response_class=HTMLResponse)
def supplier_new(request: Request) -> HTMLResponse:
    """新增供應商。"""
    return render(request, "form", values=DEFAULTS, supplier=None, errors=[])


@router.get("/{supplier_id}", response_class=HTMLResponse)
def supplier_detail(request: Request, supplier_id: str, result: str = "") -> HTMLResponse:
    """檢視資料，依權限提供修改及停用。"""
    return render(
        request,
        "detail",
        supplier=service.get_supplier(identifier(supplier_id)),
        message=MESSAGES.get(result, ""),
        error="",
    )


@router.get("/{supplier_id}/edit", response_class=HTMLResponse)
def supplier_edit(request: Request, supplier_id: str) -> HTMLResponse:
    """修改表單帶入資料版本。"""
    supplier = service.get_supplier(identifier(supplier_id))
    return render(request, "form", supplier=supplier, values=supplier, errors=[])


def saved_response(
    request: Request, supplier_id: UUID, result: str
) -> HTMLResponse | RedirectResponse:
    """只有寫入權限的使用者仍可完成操作，不因此取得查看許可。"""
    if request.state.supplier_allowed["view"]:
        return RedirectResponse(f"/suppliers/{supplier_id}?result={result}", 303)
    return render(request, "saved", message=MESSAGES[result])


async def save_form(
    request: Request, supplier_id: UUID | None = None
) -> HTMLResponse | RedirectResponse:
    """白名單資料驗證，失敗保留輸入。"""
    form = await checked_form(request, {*SupplierInput.model_fields, "updated_at"})
    supplier = {"supplier_id": str(supplier_id)} if supplier_id else None
    try:
        data = SupplierInput.model_validate(
            {key: value for key, value in form.items() if key != "updated_at"}
        )
        result_id = service.save(
            data,
            request.state.supplier_identity.user_id,
            supplier_id,
            str(form.get("updated_at", "")),
        )
    except (ValidationError, service.SupplierRuleError) as error:
        errors = (
            [str(error)]
            if isinstance(error, service.SupplierRuleError)
            else [
                "請確認必填資料、欄位長度及付款條件；台灣統編須八碼，預付／分期須依合約並填備註。"
            ]
        )
        return render(request, "form", 400, values=form, supplier=supplier, errors=errors)
    return saved_response(request, result_id, "saved" if supplier_id else "created")


@router.post("", response_model=None)
async def supplier_create(request: Request) -> HTMLResponse | RedirectResponse:
    """新增由目前 Session 記錄建立者。"""
    return await save_form(request)


@router.post("/{supplier_id}", response_model=None)
async def supplier_update(request: Request, supplier_id: str) -> HTMLResponse | RedirectResponse:
    """更新既有供應商。"""
    return await save_form(request, identifier(supplier_id))


@router.post("/{supplier_id}/status", response_model=None)
async def supplier_status(request: Request, supplier_id: str) -> HTMLResponse | RedirectResponse:
    """獨立停用／啟用許可，停用必須明確確認。"""
    form = await checked_form(request, {"active", "confirm", "updated_at"})
    key = identifier(supplier_id)
    try:
        if form.get("active") not in {"0", "1"}:
            raise service.SupplierRuleError("無效的狀態操作")
        active = form["active"] == "1"
        if not active and form.get("confirm") != "yes":
            raise service.SupplierRuleError("請先勾選確認停用，再送出")
        service.set_status(
            key, active, str(form.get("updated_at", "")), request.state.supplier_identity.user_id
        )
    except service.SupplierRuleError as error:
        return render(request, "saved", 400, message=str(error))
    return saved_response(request, key, "enabled" if active else "disabled")
