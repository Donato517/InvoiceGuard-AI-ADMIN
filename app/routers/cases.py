"""模組名稱：app.routers.cases
功能說明：真實發票案件唯讀檢視——列表、詳情、原圖（第一階段整合驗證，不含核准、退回或任何寫入）。
路由：/cases（只有 GET；沒有建立、修改或刪除路由）
注意事項：
    - 需登入，且目前部門有「發票」功能（inv.upload）查看權限；之後再由案件範圍政策（CasePolicy）決定可讀案件。
    - 案件層級規則尚未核定，預設政策拒絕所有人（403），因此正式使用者目前讀不到任何案件。
    - 操作者一律取自已驗證身分；原圖路徑只來自 DB 的 invoice_files，路由不接受任何檔案路徑參數。
    - 此為過渡性驗證頁，不取代 F 組的 /invoices 財會畫面。
"""

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.core.templating import templates
from app.routers.e_admin import session_evidence
from app.schemas.case import (
    CASE_STATUS_LABELS,
    CHANGE_SOURCE_LABELS,
    TAX_TYPE_LABELS,
    CaseError,
    CaseScope,
)
from app.schemas.e_admin import Action, Target
from app.services import case_service
from app.services.e_admin_service import INVOICES, EModule, get_e_module

Module = Annotated[EModule, Depends(get_e_module)]
Policy = Annotated[case_service.CasePolicy, Depends(case_service.get_case_policy)]


def require_case_access(request: Request, module: Module, policy: Policy) -> None:
    """驗證登入與發票功能查看權限，再取得案件範圍；任何一步失敗都不放行。"""
    identity = module.identity(session_evidence(request))
    module.check(identity, INVOICES, Action.VIEW, Target(identity.selected_department_id))
    departments = module.departments(identity)
    menu = module.sidebar_menu(identity)
    scope = policy.scope(identity, departments)
    request.state.e_configuration_authenticated = True
    request.state.e_configuration_menu = menu
    request.state.header_departments = departments
    request.state.header_selected_department_id = identity.selected_department_id
    request.state.case_scope = scope


def _case_json(value: object) -> str:
    """把 JSONB 值轉成可讀文字（保留中文；HTML 由 Jinja 自動跳脫）。"""
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


templates.env.filters["case_json"] = _case_json

router = APIRouter(prefix="/cases", tags=["cases"], dependencies=[Depends(require_case_access)])


def _scope(request: Request) -> CaseScope:
    scope: CaseScope = request.state.case_scope
    return scope


def _uuid(value: str) -> UUID:
    """路徑參數須為 UUID；格式不符視為找不到，不送進 SQL。"""
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(status_code=404, detail="找不到指定案件") from None


def _http(error: CaseError) -> HTTPException:
    return HTTPException(status_code=error.status, detail=error.message)


@router.get("", response_class=HTMLResponse)
def case_list(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=case_service.MAX_PAGE_SIZE)] = 20,
    status: str | None = None,
) -> HTMLResponse:
    """案件列表：前台已提交（非草稿）的真實案件，最新送出在前。"""
    try:
        result = case_service.list_cases(
            _scope(request), page=page, page_size=page_size, status=status or None
        )
    except CaseError as error:
        raise _http(error) from None
    return templates.TemplateResponse(
        request,
        "pages/cases/list.html",
        {
            "page": result,
            "status": status or "",
            "status_labels": CASE_STATUS_LABELS,
            "status_options": [(k, v) for k, v in CASE_STATUS_LABELS.items() if k != "draft"],
            "has_next": result.page * result.page_size < result.total,
        },
    )


@router.get("/{invoice_id}", response_class=HTMLResponse)
def case_detail(request: Request, invoice_id: str) -> HTMLResponse:
    """案件詳情：表頭、明細、AI 辨識、人工修正、送出當下檢核、狀態歷程與原圖。"""
    try:
        detail = case_service.get_case_detail(_scope(request), _uuid(invoice_id))
    except CaseError as error:
        raise _http(error) from None
    return templates.TemplateResponse(
        request,
        "pages/cases/detail.html",
        {
            "detail": detail,
            "status_labels": CASE_STATUS_LABELS,
            "tax_type_labels": TAX_TYPE_LABELS,
            "source_labels": CHANGE_SOURCE_LABELS,
        },
    )


@router.get("/{invoice_id}/image")
def case_image(request: Request, invoice_id: str) -> Response:
    """案件目前有效的原始影像；經伺服器驗權後由私有 Storage 讀出，不提供公開網址。

    失敗時：頁面腳本以 Accept: application/json 查詢原因，只回固定的 code 與中文訊息（no_image／
    image_unreadable／temporary 等），不含路徑或內部錯誤；其他請求（例如在新分頁開啟 PDF）沿用既有錯誤頁。
    """
    try:
        content, mime_type = case_service.get_case_image(_scope(request), _uuid(invoice_id))
    except CaseError as error:
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse(
                {"code": error.code, "message": error.message},
                status_code=error.status,
                headers={"Cache-Control": "private, no-store"},
            )
        raise _http(error) from None
    return Response(
        content=content,
        media_type=mime_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )
