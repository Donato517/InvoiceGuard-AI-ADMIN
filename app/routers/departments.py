"""模組名稱：app.routers.departments
功能說明：沿用 ADMIN 版型提供部門管理，本階段只限本機開發。
路由：GET/POST /department、POST /department/{department_id}/name、/code、/status
注意事項：本機也必須經 E 身分／授權接點；資料與交易接線未完成時拒絕，雲端關閉。
"""

from ipaddress import ip_address
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.security import verify_same_origin
from app.core.templating import templates
from app.routers.e_admin import require_configuration_access
from app.services import department_service as service


def require_local_departments(request: Request) -> None:
    """同時檢查部署模式、連線來源與 Host，不信任轉送來源標頭。"""
    if settings.APP_ENV != "local" or settings.is_vercel or settings.module_backend != "remote":
        raise NotFoundError
    try:
        local_peer = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local_peer = False
    if not local_peer or request.url.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
        "invoiceguard-ai-admin.localhost",
    }:
        raise NotFoundError


def require_department_origin(request: Request) -> None:
    """匿名本機寫入要求完整同源 Origin，拒絕缺漏來源的瀏覽器請求。"""
    if request.headers.get("origin") != f"{request.url.scheme}://{request.headers.get('host')}":
        raise ForbiddenError("部門操作必須從本機管理頁面送出")


router = APIRouter(
    prefix="/department",
    tags=["departments"],
    dependencies=[Depends(require_local_departments), Depends(require_configuration_access)],
)
WRITE_CHECKS = [Depends(verify_same_origin), Depends(require_department_origin)]


@router.get("", response_class=HTMLResponse)
def department_list(request: Request, q: str = "") -> HTMLResponse:
    """顯示部門列表及搜尋表單。"""
    return _render(request, q=q)


@router.post("", response_class=HTMLResponse, dependencies=WRITE_CHECKS)
def department_create(
    request: Request, code: Annotated[str, Form()], name: Annotated[str, Form()]
) -> HTMLResponse:
    """新增部門並顯示操作結果。"""
    try:
        service.create(code, name)
    except service.DepartmentError as error:
        return _render(request, error=str(error), code=code, name=name)
    return _render(request, message="已新增部門")


@router.post("/{department_id}/name", response_class=HTMLResponse, dependencies=WRITE_CHECKS)
def department_rename(
    request: Request, department_id: UUID, name: Annotated[str, Form()]
) -> HTMLResponse:
    """修改名稱，不接受代碼變更。"""
    try:
        service.rename(department_id, name)
    except service.DepartmentError as error:
        return _render(request, error=str(error))
    return _render(request, message="已修改部門名稱")


@router.post("/{department_id}/status", response_class=HTMLResponse, dependencies=WRITE_CHECKS)
def department_status(
    request: Request, department_id: UUID, active: Annotated[bool, Form()]
) -> HTMLResponse:
    """啟用或停用部門，不刪除資料。"""
    try:
        service.set_active(department_id, active)
    except service.DepartmentError as error:
        return _render(request, error=str(error))
    return _render(request, message="已啟用部門" if active else "已停用部門")


@router.post("/{department_id}/code", response_class=HTMLResponse, dependencies=WRITE_CHECKS)
def department_change_code(
    request: Request, department_id: UUID, code: Annotated[str, Form()]
) -> HTMLResponse:
    """修改代碼並顯示結果，套用既有本機與同源檢查。"""
    try:
        service.change_code(department_id, code)
    except service.DepartmentError as error:
        return _render(request, error=str(error))
    return _render(request, message="已修改部門代碼")


def _render(
    request: Request,
    *,
    q: str = "",
    message: str = "",
    error: str = "",
    code: str = "",
    name: str = "",
) -> HTMLResponse:
    departments = []
    try:
        departments = service.search(q)
    except service.DepartmentError as caught:
        error = f"{error}；{caught}" if error else str(caught)
    return templates.TemplateResponse(
        request,
        "pages/departments/list.html",
        {
            "departments": departments,
            "q": q,
            "message": message,
            "error": error,
            "code": code,
            "name": name,
        },
        headers={"Cache-Control": "no-store"},
    )
