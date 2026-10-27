"""模組名稱：app.routers.e_admin；功能說明：E 正式管理畫面，無 /dev 或本機繞過。

路由：/workspace、/system/departments、/system/features、/account、/auth/password/*。
"""

from typing import Annotated
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import SecretStr, ValidationError

from app.constants import SESSION_COOKIE_NAME
from app.core.security import clear_session_cookie
from app.core.templating import templates
from app.schemas.e_admin import (
    Action,
    DeniedError,
    DepartmentInput,
    EError,
    FeatureInput,
    GrantRow,
    Identity,
    InputModel,
    MatrixChange,
    ProfilePatch,
    Target,
    UnavailableError,
)
from app.services.e_admin_service import DEPARTMENTS, FEATURES, GRANTS, EModule, get_e_module
from app.services.e_registry import supported_actions

router = APIRouter(tags=["06_E"])
Module = Annotated[EModule, Depends(get_e_module)]


def require_pending_admin_integration() -> None:
    """使用者核定暫停來源帳號／部門入口；正式職務與 scope 接妥前一律拒絕。"""
    raise UnavailableError


def session_evidence(request: Request) -> SecretStr | None:
    """僅讀 ADMIN Cookie，不接受 URL／Header 自稱 UUID、職務或 grants。"""
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    return SecretStr(raw) if raw else None


def current_identity(request: Request, module: Module) -> Identity:
    """不經舊 int Cookie 驗證器；所有 E 頁每次重新向共用 adapter 驗證。"""
    identity = module.identity(session_evidence(request))
    request.state.e_access = module.workspace_access(identity)
    return identity


CurrentIdentity = Annotated[Identity, Depends(current_identity)]


def require_e_origin(request: Request) -> None:
    """變更只接受精確同源瀏覽器 POST；缺 Origin／Referer 亦拒絕。"""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin or request.headers.get("sec-fetch-site") == "cross-site":
        raise DeniedError
    try:
        parsed = urlsplit(origin)
    except ValueError:
        raise DeniedError from None
    if parsed.scheme != request.url.scheme or parsed.netloc.lower() != request.url.netloc.lower():
        raise DeniedError
    if parsed.username or parsed.password:
        raise DeniedError


async def handle_e_error(request: Request, error: EError) -> HTMLResponse | RedirectResponse:
    """只顯示固定訊息；不輸出 adapter 例外、密碼或 SQL 參數。"""
    if error.status == 401:
        response = RedirectResponse(
            "/login?" + urlencode({"next": request.url.path}), status_code=303
        )
        clear_session_cookie(response)
    else:
        response = templates.TemplateResponse(
            request,
            "pages/errors/error.html",
            {
                "status_code": error.status,
                "message": error.message,
            },
            status_code=error.status,
        )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _render(
    request: Request,
    module: EModule,
    identity: Identity,
    template: str,
    values: dict[str, object] | None = None,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request,
        "pages/e_admin/" + template,
        {
            "identity": identity,
            "e_menu": tuple(item for item in request.state.e_access.functions if item.can_open),
            "effective_access": request.state.e_access,
            **(values or {}),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _allowed(
    module: EModule, identity: Identity, code: str, action: Action, target: Target
) -> bool:
    try:
        module.check(identity, code, action, target)
    except DeniedError:
        return False
    return True


async def _input[InputType: InputModel](
    request: Request, model: type[InputType]
) -> tuple[InputType, str | None]:
    form = await request.form()
    if any(len(form.getlist(key)) != 1 for key in form):
        raise EError
    values = dict(form)
    revision = values.pop("expected_revision", None)
    if revision is not None and (
        not isinstance(revision, str) or not revision or len(revision) > 200
    ):
        raise EError
    if model in {DepartmentInput, FeatureInput}:
        values.setdefault("is_active", False)
    try:
        return model.model_validate(values), revision
    except ValidationError:
        raise EError from None


@router.get("/workspace", response_class=HTMLResponse)
def workspace(request: Request, module: Module, identity: CurrentIdentity) -> HTMLResponse:
    """僅顯示有效管理功能；無可執行功能由共同相依檢查拒絕。"""
    return _render(request, module, identity, "workspace.html")


@router.get("/system/departments", response_class=HTMLResponse)
def department_list(request: Request, module: Module, identity: CurrentIdentity) -> HTMLResponse:
    """部門列表及依權限顯示的新增表單。"""
    items = module.list_departments(identity)
    return _render(
        request,
        module,
        identity,
        "departments.html",
        {
            "items": items,
            "selected": None,
            "can_save": _allowed(
                module, identity, DEPARTMENTS, Action.CREATE, Target(global_resource=True)
            ),
        },
    )


@router.get("/system/departments/{department_id}", response_class=HTMLResponse)
def department_detail(
    request: Request,
    department_id: UUID,
    module: Module,
    identity: CurrentIdentity,
) -> HTMLResponse:
    """修改頁依目標部門範圍驗權；不以表單中的部門 ID 覆蓋路徑。"""
    selected = module.get_department(identity, department_id)
    return _render(
        request,
        module,
        identity,
        "departments.html",
        {
            "items": (),
            "selected": selected,
            "can_save": _allowed(module, identity, DEPARTMENTS, Action.EDIT, Target(department_id)),
            "can_grants": _allowed(module, identity, GRANTS, Action.VIEW, Target(department_id)),
        },
    )


@router.post("/system/departments", dependencies=[Depends(require_e_origin)])
async def department_create(
    request: Request, module: Module, identity: CurrentIdentity
) -> RedirectResponse:
    """由 service 交易新增；不使用 /dev 或直接從 router 操作 DB。"""
    data, _ = await _input(request, DepartmentInput)
    module.save_department(identity, data)
    return RedirectResponse(
        "/system/departments", status_code=303, headers={"Cache-Control": "no-store"}
    )


@router.post("/system/departments/{department_id}", dependencies=[Depends(require_e_origin)])
async def department_update(
    request: Request,
    department_id: UUID,
    module: Module,
    identity: CurrentIdentity,
) -> RedirectResponse:
    """以版本保護更新，停用使用中的部門回 409。"""
    data, revision = await _input(request, DepartmentInput)
    module.save_department(identity, data, department_id=department_id, expected_revision=revision)
    return RedirectResponse(
        "/system/departments", status_code=303, headers={"Cache-Control": "no-store"}
    )


@router.get("/system/features", response_class=HTMLResponse)
def feature_list(request: Request, module: Module, identity: CurrentIdentity) -> HTMLResponse:
    """功能目錄；分類／route／action 為固定 registry 的資訊。"""
    items = module.list_features(identity)
    return _render(
        request,
        module,
        identity,
        "features.html",
        {
            "items": items,
            "registry": module.registry,
            "selected": None,
            "can_save": _allowed(
                module, identity, FEATURES, Action.CREATE, Target(global_resource=True)
            ),
        },
    )


@router.get("/system/features/{function_id}", response_class=HTMLResponse)
def feature_detail(
    request: Request, function_id: UUID, module: Module, identity: CurrentIdentity
) -> HTMLResponse:
    """既有功能代碼唯讀，避免既有矩陣改接其他能力。"""
    selected = module.get_feature(identity, function_id)
    return _render(
        request,
        module,
        identity,
        "features.html",
        {
            "items": (),
            "registry": module.registry,
            "selected": selected,
            "can_save": _allowed(
                module, identity, FEATURES, Action.EDIT, Target(global_resource=True)
            ),
        },
    )


@router.post("/system/features", dependencies=[Depends(require_e_origin)])
async def feature_create(
    request: Request, module: Module, identity: CurrentIdentity
) -> RedirectResponse:
    """新增字典；未知程式能力只能維持停用。"""
    data, _ = await _input(request, FeatureInput)
    module.save_feature(identity, data)
    return RedirectResponse(
        "/system/features", status_code=303, headers={"Cache-Control": "no-store"}
    )


@router.post("/system/features/{function_id}", dependencies=[Depends(require_e_origin)])
async def feature_update(
    request: Request,
    function_id: UUID,
    module: Module,
    identity: CurrentIdentity,
) -> RedirectResponse:
    """版本檢查、稽核及功能失效均由共同交易保護。"""
    data, revision = await _input(request, FeatureInput)
    module.save_feature(identity, data, function_id=function_id, expected_revision=revision)
    return RedirectResponse(
        "/system/features", status_code=303, headers={"Cache-Control": "no-store"}
    )


@router.get("/system/departments/{department_id}/permissions", response_class=HTMLResponse)
def matrix_page(
    request: Request, department_id: UUID, module: Module, identity: CurrentIdentity
) -> HTMLResponse:
    """同源 G 矩陣；不把部門 can_view/edit 當成職務或資料範圍授權。"""
    matrix = module.read_matrix(identity, department_id)
    current = {row.function_code: row.actions for row in matrix.rows}
    ceiling = {row.function_code: row.actions for row in matrix.delegable}
    cells = [
        {
            "capability": item,
            "actions": [
                {
                    "name": action.value,
                    "checked": action in current.get(item.code, ()),
                    "delegable": action in ceiling.get(item.code, ()),
                }
                for action in Action
                if action in item.actions
            ],
        }
        for item in module.registry
    ]
    return _render(
        request,
        module,
        identity,
        "matrix.html",
        {
            "department_id": department_id,
            "matrix": matrix,
            "cells": cells,
            "can_save": _allowed(module, identity, GRANTS, Action.EDIT, Target(department_id)),
        },
    )


@router.post(
    "/system/departments/{department_id}/permissions", dependencies=[Depends(require_e_origin)]
)
async def matrix_update(
    request: Request, department_id: UUID, module: Module, identity: CurrentIdentity
) -> RedirectResponse:
    """只更新 registry 中提交的列；未註冊的其他組功能由 G 保留。"""
    form = await request.form()
    if set(form) - {"grant", "expected_revision"} or len(form.getlist("expected_revision")) != 1:
        raise EError
    selected: dict[str, set[Action]] = {item.code: set() for item in module.registry}
    try:
        values = form.getlist("grant")
        if len(set(values)) != len(values):
            raise ValueError
        for value in values:
            code, name = str(value).rsplit(":", 1)
            action = Action(name)
            if code not in selected or action not in supported_actions(code, module.registry):
                raise ValueError
            selected[code].add(action)
        change = MatrixChange(
            expected_revision=form["expected_revision"],
            rows=tuple(
                GrantRow(function_code=code, actions=frozenset(actions))
                for code, actions in selected.items()
            ),
        )
    except (ValueError, TypeError):
        raise EError from None
    module.save_matrix(identity, department_id, change)
    return RedirectResponse(
        f"/system/departments/{department_id}/permissions",
        status_code=303,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/account", response_class=HTMLResponse)
def profile_page(request: Request, module: Module, identity: CurrentIdentity) -> HTMLResponse:
    """本人基本資訊，絕不接受另一位使用者 UUID。"""
    page = module.profile(identity)
    request.state.e_access = page.access
    return _render(request, module, identity, "profile.html", {"profile": page.profile})


@router.post("/account", dependencies=[Depends(require_e_origin)])
async def profile_update(
    request: Request, module: Module, identity: CurrentIdentity
) -> RedirectResponse:
    """只允許使用者已核定的顯示名稱；正式寫入委派共同 adapter。"""
    data, _ = await _input(request, ProfilePatch)
    module.update_profile(identity, data)
    return RedirectResponse("/account", status_code=303, headers={"Cache-Control": "no-store"})


@router.get("/auth/password/forgot", response_class=HTMLResponse)
def forgot_page(request: Request) -> HTMLResponse:
    """顯示共用忘記密碼入口；GET 不寄信、不產生 Token。"""
    return templates.TemplateResponse(
        request, "pages/e_admin/forgot.html", headers={"Cache-Control": "no-store"}
    )


@router.post("/auth/password/forgot", dependencies=[Depends(require_e_origin)])
async def forgot_submit(request: Request, module: Module) -> HTMLResponse:
    """B 成功受理後才顯示通用訊息，來源未接線回 503。"""
    form = await request.form()
    if (
        set(form) != {"email"}
        or len(form.getlist("email")) != 1
        or not isinstance(form["email"], str)
    ):
        raise EError
    module.request_password_reset(form["email"])
    return templates.TemplateResponse(
        request,
        "pages/e_admin/forgot.html",
        {
            "message": "若帳號符合條件，系統會寄出重設密碼指示。",
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/auth/password/change", dependencies=[Depends(require_e_origin)])
async def password_change(
    request: Request, module: Module, identity: CurrentIdentity
) -> RedirectResponse:
    """委派 B 變更憑證後清除 Cookie，要求重新登入。"""
    form = await request.form()
    if set(form) != {"current_password", "new_password"} or any(
        len(form.getlist(key)) != 1 or not isinstance(form[key], str) or not form[key]
        for key in form
    ):
        raise EError
    module.change_password(
        identity, SecretStr(form["current_password"]), SecretStr(form["new_password"])
    )
    response = RedirectResponse("/login", status_code=303, headers={"Cache-Control": "no-store"})
    clear_session_cookie(response)
    return response
