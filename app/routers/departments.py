"""模組名稱：app.routers.departments
功能說明：沿用 ADMIN 版型提供部門管理，本階段只限本機開發。
路由：GET/POST /department、POST /department/{department_id}/name、/code、/status
注意事項：核定本機初始化暫免登入；正式身分／授權仍待接線，雲端關閉。
"""

from ipaddress import ip_address
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.constants import WELCOME_CODE
from app.core.config import settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.security import verify_same_origin
from app.core.templating import templates
from app.routers.e_admin import require_configuration_access, require_e_origin
from app.schemas.department_detail import DepartmentDetail, DepartmentDetailInput
from app.schemas.e_admin import Action, EError, MissingError
from app.services import department_detail_service as detail_service
from app.services import department_service as service
from app.services.e_registry import supported_actions


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
    request: Request,
    department_id: UUID,
    active: Annotated[bool, Form()],
    q: Annotated[str, Form(max_length=200)] = "",
) -> HTMLResponse:
    """沿用停用限制及驗權，保留列表搜尋；資料與授權不刪除。"""
    try:
        service.set_active(department_id, active)
    except service.DepartmentError as error:
        return _render(request, q=q, error=str(error))
    return _render(request, q=q, message="已啟用部門" if active else "已停用部門")


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


def _detail_response(
    request: Request,
    detail: DepartmentDetail,
    *,
    values: dict[str, object] | None = None,
    grants: dict[UUID, frozenset[Action]] | None = None,
    expected_revision: str | None = None,
    error: EError | None = None,
    saved: bool = False,
) -> HTMLResponse:
    """回填草稿並保留原指紋；失敗不自動重設成新版本。"""
    return templates.TemplateResponse(
        request,
        "pages/departments/detail.html",
        {
            "department": detail.department,
            "values": values if values is not None else detail.department.model_dump(),
            "rows": tuple(
                row for row in detail.matrix.rows if row.function.function_code != WELCOME_CODE
            ),
            "welcome_rows": tuple(
                row for row in detail.matrix.rows if row.function.function_code == WELCOME_CODE
            ),
            "grants": grants
            if grants is not None
            else {r.function.function_id: r.actions for r in detail.matrix.rows},
            "revision": expected_revision
            if expected_revision is not None
            else detail_service.revision(detail),
            "actions": [
                (a, label)
                for a, label in zip(Action, ["查看", "新增", "修改", "刪除"], strict=True)
            ],
            "supported_actions": supported_actions,
            "error": error.message if error else "",
            "saved": saved,
            "blocked": error is not None and error.status in {409, 503},
        },
        status_code=error.status if error else 200,
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
    )


@router.get("/{department_id}/edit", response_class=HTMLResponse)
def department_edit(request: Request, department_id: UUID, saved: bool = False) -> HTMLResponse:
    """部門內容與授權功能同頁，不再切換獨立矩陣頁。"""
    return _detail_response(request, detail_service.read_detail(department_id), saved=saved)


@router.post("/{department_id}/edit", dependencies=[Depends(require_e_origin), *WRITE_CHECKS])
async def department_save_detail(request: Request, department_id: UUID) -> HTMLResponse:
    """嚴格解析完整功能列與四操作；不接受指定其他目標或任意欄位。"""
    form = await request.form(max_fields=6000)
    allowed = {
        "department_code",
        "department_name",
        "is_active",
        "expected_revision",
        "catalog_complete",
        "function_id",
        "grant",
    }
    if set(form) - allowed or any(
        len(form.getlist(key)) != 1 for key in form if key not in {"function_id", "grant"}
    ):
        raise EError
    if form.get("catalog_complete") != "true":
        raise EError
    values = {
        k: form[k] for k in ["department_code", "department_name", "expected_revision"] if k in form
    }
    values["is_active"] = form.get("is_active", "false")
    try:
        ids = [UUID(str(value)) for value in form.getlist("function_id")]
        if len(ids) > 1000 or len(ids) != len(set(ids)):
            raise ValueError
        selected: dict[UUID, set[Action]] = {value: set() for value in ids}
        raw_grants = form.getlist("grant")
        if len(raw_grants) != len(set(raw_grants)):
            raise ValueError
        for raw in raw_grants:
            function_id, action = str(raw).rsplit(":", 1)
            selected[UUID(function_id)].add(Action(action))
        grants = {key: frozenset(value) for key, value in selected.items()}
    except (ValueError, KeyError, TypeError):
        raise EError from None
    try:
        data = DepartmentDetailInput.model_validate(values)
    except ValidationError:
        return _detail_response(
            request,
            detail_service.read_detail(department_id),
            values=values,
            grants=grants,
            expected_revision=str(values.get("expected_revision", "")),
            error=EError(),
        )
    try:
        detail_service.save_detail(department_id, data, grants)
    except MissingError:
        raise
    except EError as error:
        return _detail_response(
            request,
            detail_service.read_detail(department_id),
            values=data.model_dump(),
            grants=grants,
            expected_revision=data.expected_revision,
            error=error,
        )
    return RedirectResponse(
        f"/department/{department_id}/edit?saved=true",
        status_code=303,
        headers={"Cache-Control": "no-store"},
    )
