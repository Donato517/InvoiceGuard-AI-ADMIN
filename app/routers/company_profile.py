"""本公司基本資料（名稱、統一編號、稅籍編號）；比照正式供應商管理的驗權與交易方式，無本機免登入例外。"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.core.templating import templates
from app.routers.e_admin import require_e_origin, session_evidence
from app.schemas.company_profile import CompanyProfileInput
from app.schemas.e_admin import Action, DeniedError, Target, UnavailableError
from app.schemas.tw_postal_codes import POSTAL_CODES
from app.services import company_profile_management_service as service
from app.services.admin_login_service import DepartmentLoginAdapter
from app.services.e_admin_service import EModule, get_e_module
from app.services.e_configuration_security import (
    ConfigurationContext,
    enter_configuration,
    leave_configuration,
)

# 程式代碼；DB 功能目錄為 company_profile（admin_login_service.CATALOG_CODES 對應）
CODE = "company.profile"
Module = Annotated[EModule, Depends(get_e_module)]
# 給前端連動用的有序陣列；JSON 物件的鍵會被 tojson 重新排序，所以不用 dict
POSTAL_OPTIONS = [[city, list(districts.items())] for city, districts in POSTAL_CODES.items()]
# 稅籍編號要輸入兩次，一致才能存檔
CONFIRM_FIELD = "tax_reg_no_confirm"


async def require_company_profile_access(request: Request, module: Module) -> AsyncIterator[None]:
    """每個固定路由依操作驗權，並將既有 Session 交給交易內重驗。"""
    action = {
        "company_profile_detail": Action.VIEW,
        "company_profile_edit": Action.EDIT,
        "company_profile_save": Action.EDIT,
    }.get(request.scope["route"].name)
    if action is None or not isinstance(module.access, DepartmentLoginAdapter):
        raise UnavailableError
    identity = module.identity(session_evidence(request))
    target = Target(global_resource=True)
    module.check(identity, CODE, action, target)
    if request.method != "GET":
        require_e_origin(request)
    allowed = {}
    for option in (Action.VIEW, Action.EDIT):
        try:
            module.check(identity, CODE, option, target)
            allowed[option.value] = True
        except DeniedError:
            allowed[option.value] = False
    request.state.company_profile_allowed = allowed
    request.state.company_profile_identity = identity
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
    prefix="/company-profile",
    tags=["company-profile"],
    dependencies=[Depends(require_company_profile_access)],
)


def render(
    request: Request, template_name: str, status: int = 200, **context: object
) -> HTMLResponse:
    """依權限呈現修改入口；表單另需縣市與鄉鎮市區清單。"""
    return templates.TemplateResponse(
        request,
        "pages/company_profile/" + template_name + ".html",
        {
            "can": request.state.company_profile_allowed,
            "postal_codes": POSTAL_CODES,
            "postal_options": POSTAL_OPTIONS,
            **context,
        },
        status_code=status,
    )


@router.get("", response_class=HTMLResponse)
def company_profile_detail(request: Request, result: str = "") -> HTMLResponse:
    """查看公司基本資料；尚未設定時提示到修改頁設定。"""
    return render(
        request,
        "detail",
        profile=service.get_profile(),
        message="已儲存公司基本資料" if result == "saved" else "",
    )


@router.get("/edit", response_class=HTMLResponse)
def company_profile_edit(request: Request) -> HTMLResponse:
    """設定或修改表單；稅籍編號確認欄不帶入原值，要重新輸入。"""
    profile = service.get_profile()
    return render(request, "form", values=profile or {}, profile=profile, errors=[])


@router.post("", response_model=None)
async def company_profile_save(request: Request) -> HTMLResponse | RedirectResponse:
    """白名單欄位；稅籍編號兩次一致、統編檢查碼通過才寫入，操作者取自 Session。"""
    form = await request.form(max_fields=20)
    allowed = {*CompanyProfileInput.model_fields, CONFIRM_FIELD, "updated_at"}
    if set(form) - allowed or any(len(form.getlist(key)) != 1 for key in form):
        raise HTTPException(400, "表單包含未知或重複欄位，請重新開啟畫面")
    values = {key: str(value) for key, value in form.items()}
    revision = values.get("updated_at", "")
    profile = {"updated_at": revision} if revision else None
    try:
        data = CompanyProfileInput.model_validate(
            {key: values.get(key, "") for key in CompanyProfileInput.model_fields}
        )
        if values.get(CONFIRM_FIELD, "").strip() != data.tax_reg_no:
            raise service.CompanyProfileRuleError("兩次輸入的稅籍編號不一致，請再確認")
        service.save(data, request.state.company_profile_identity.user_id, revision)
    except (ValidationError, service.CompanyProfileRuleError) as error:
        errors = (
            [str(error)]
            if isinstance(error, service.CompanyProfileRuleError)
            else [
                "請確認公司名稱已填寫、統一編號為 8 碼數字、稅籍編號為 9 碼數字；"
                "營業地址的縣市與鄉鎮市區須從清單選擇；選填欄位不可超過長度，Email 格式須正確。"
            ]
        )
        values.pop(CONFIRM_FIELD, None)
        return render(request, "form", 400, values=values, profile=profile, errors=errors)
    if request.state.company_profile_allowed["view"]:
        return RedirectResponse("/company-profile?result=saved", 303)
    return render(request, "saved", message="已儲存公司基本資料")
