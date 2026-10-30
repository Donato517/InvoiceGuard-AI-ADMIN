"""
模組名稱：app.routers.knowledge
功能說明：知識庫與文件管理——列表、文件清單、上傳對話框、部門指派與問答選用、審核、索引、編輯與下載。
路由：/knowledge
注意事項：
    - 本路由需目前部門有 RAG知識庫管理（rag_manager）的查看權限（require_knowledge_access）；
      寫入路由另以 module.check 依操作檢查 CREATE、EDIT 或 DELETE；審核另需目前部門為資訊部。
    - 資料來自 DB（rag_admin_service），部門清單讀 departments；本路由不直接存取 DB、Storage 或模型。
    - 操作者一律取自已驗證身分（request.state.knowledge_identity），不接受表單自報的使用者或上傳部門。
"""

from collections.abc import Sequence
from html import escape
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.templating import templates
from app.routers.e_admin import session_evidence
from app.schemas.e_access import EffectiveDepartment
from app.schemas.e_admin import Action, DeniedError, Target
from app.schemas.rag import (
    APPROVAL_APPROVED,
    APPROVAL_LABELS,
    ERROR_LABELS,
    JOB_FAILED,
    PARSE_FAILED,
    PARSE_LABELS,
    PARSE_READY,
    PARSE_UPLOADED,
    TEXT_EDITABLE_EXTENSIONS,
    DepartmentAccess,
    DepartmentOption,
    KnowledgeBase,
    RagActor,
    RagFile,
    RagRuleError,
)
from app.services import rag_admin_service as rag_service
from app.services.e_admin_service import KNOWLEDGE, EModule, get_e_module
from app.services.rag_admin_service import RagUnavailableError, UploadItem
from app.services.rag_selection_service import can_manage_knowledge_base

Module = Annotated[EModule, Depends(get_e_module)]

# 資訊部的部門代碼（departments.department_code）
IT_DEPARTMENT_CODE = "technology_d"


def _is_it_department(
    departments: Sequence[EffectiveDepartment], selected_department_id: UUID
) -> bool:
    return any(
        dept.department_id == selected_department_id
        and (dept.department_code == IT_DEPARTMENT_CODE or "資訊" in dept.department_name)
        for dept in departments
    )


def require_knowledge_access(request: Request, module: Module) -> None:
    """驗證目前部門可查看知識庫，並把身分放入 request.state 供後續路由使用。"""
    identity = module.identity(session_evidence(request))
    module.check(identity, KNOWLEDGE, Action.VIEW, Target(identity.selected_department_id))
    departments = module.departments(identity)
    menu = module.sidebar_menu(identity)
    is_it_workspace = _is_it_department(departments, identity.selected_department_id)
    # 所有驗權與顯示資料都成功取得後，才一次發佈本次請求的登入狀態。
    request.state.e_configuration_authenticated = True
    request.state.e_configuration_menu = menu
    request.state.header_departments = departments
    request.state.header_selected_department_id = identity.selected_department_id
    request.state.knowledge_identity = identity
    request.state.is_it_workspace = is_it_workspace


router = APIRouter(
    prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(require_knowledge_access)]
)


# ---- 身分、權限與錯誤 ----


def _viewer(request: Request) -> RagActor:
    """目前操作者（查看用，已由 require_knowledge_access 驗過查看權限）。"""
    identity = request.state.knowledge_identity
    return RagActor(
        user_id=str(identity.user_id),
        department_id=str(identity.selected_department_id),
        is_it=bool(getattr(request.state, "is_it_workspace", False)),
    )


def _authorize(request: Request, module: EModule, action: Action) -> RagActor:
    """寫入前依操作檢查目前部門的知識庫功能權限。"""
    identity = request.state.knowledge_identity
    module.check(identity, KNOWLEDGE, action, Target(identity.selected_department_id))
    return _viewer(request)


def _authorize_review(request: Request, module: EModule) -> RagActor:
    """審核動作：須同時有修改權限，且目前部門為資訊部。"""
    actor = _authorize(request, module, Action.EDIT)
    if not actor.is_it:
        raise DeniedError
    return actor


def _uuid(value: str) -> str:
    """路徑參數須為 UUID；格式不符視為找不到，不送進 SQL。"""
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(status_code=404, detail="找不到指定資料") from None


def _unavailable(error: RagUnavailableError) -> HTTPException:
    return HTTPException(status_code=503, detail=str(error))


def _not_found(error: RagRuleError) -> HTTPException | None:
    return (
        HTTPException(status_code=404, detail=error.message) if error.code == "NOT_FOUND" else None
    )


def _departments() -> list[DepartmentOption]:
    try:
        return rag_service.list_departments()
    except RagUnavailableError as error:
        raise _unavailable(error) from None


def _department_views(departments: Sequence[DepartmentOption]) -> list[dict[str, str]]:
    return [{"id": d.department_id, "name": d.department_name} for d in departments]


def _department_name(departments: Sequence[DepartmentOption], department_id: str) -> str:
    return next(
        (d.department_name for d in departments if d.department_id == department_id),
        "（已停用部門）",
    )


# ---- 畫面資料 ----


def _kb_badges(
    kb: KnowledgeBase,
    rows: Sequence[DepartmentAccess],
    departments: Sequence[DepartmentOption],
    actor: RagActor,
) -> list[dict[str, Any]]:
    assigned = {
        r.department_id for r in rows if r.knowledge_base_id == kb.knowledge_base_id and r.can_read
    }
    can_toggle = can_manage_knowledge_base(kb, rows, actor)
    return [
        {
            "id": d.department_id,
            "name": d.department_name,
            "state": "on" if d.department_id in assigned else "off",
            "can_toggle": can_toggle,
        }
        for d in departments
    ]


def _qa_choices(
    kbs: Sequence[KnowledgeBase],
    rows: Sequence[DepartmentAccess],
    departments: Sequence[DepartmentOption],
    actor: RagActor,
) -> list[dict[str, Any]]:
    """每個部門可選用問答的知識庫（已核准、啟用中、已指派）與目前選用。"""
    choices = []
    for dept in departments:
        mine = [r for r in rows if r.department_id == dept.department_id]
        readable = {r.knowledge_base_id for r in mine if r.can_read}
        options = [
            {"id": kb.knowledge_base_id, "title": kb.knowledge_name}
            for kb in kbs
            if kb.approval_status == APPROVAL_APPROVED
            and kb.is_active
            and kb.knowledge_base_id in readable
        ]
        current = next((r.knowledge_base_id for r in mine if r.use_for_qa), "")
        choices.append(
            {
                "id": dept.department_id,
                "name": dept.department_name,
                "options": options,
                "current": current,
                "editable": actor.is_it or dept.department_id == actor.department_id,
            }
        )
    return choices


@router.get("", response_class=HTMLResponse)
def knowledge_list(
    request: Request,
    q: Annotated[str, Query()] = "",
    department_id: Annotated[str, Query()] = "all",
) -> HTMLResponse:
    """全部門知識庫管理：統計、搜尋、部門篩選、部門指派切換、審核狀態與部門問答選用。"""
    return _render_knowledge_list(request, q=q, department_id=department_id)


def _render_knowledge_list(
    request: Request,
    *,
    q: str = "",
    department_id: str = "all",
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    actor = _viewer(request)
    departments = _departments()
    try:
        overview = rag_service.load_overview()
    except RagUnavailableError as exc:
        raise _unavailable(exc) from None
    kbs = overview.knowledge_bases
    rows = overview.access_rows

    filtered = list(kbs)
    if q.strip():
        term = q.strip().lower()
        filtered = [k for k in filtered if term in k.knowledge_name.lower()]
    if department_id != "all":
        filtered = [
            k
            for k in filtered
            if any(
                r.knowledge_base_id == k.knowledge_base_id
                and r.department_id == department_id
                and r.can_read
                for r in rows
            )
        ]

    view_rows = []
    for kb in filtered:
        mine = [r for r in rows if r.knowledge_base_id == kb.knowledge_base_id]
        view_rows.append(
            {
                "id": kb.knowledge_base_id,
                "title": kb.knowledge_name,
                "badges": _kb_badges(kb, mine, departments, actor),
                "toggle_url": f"/knowledge/{kb.knowledge_base_id}/toggle-dept",
                "approval_status": kb.approval_status,
                "approval_label": APPROVAL_LABELS.get(kb.approval_status, "待審核"),
                "review_comment": kb.review_comment,
                "qa_department_names": [
                    _department_name(departments, r.department_id) for r in mine if r.use_for_qa
                ],
                "can_manage": can_manage_knowledge_base(kb, mine, actor),
            }
        )

    # 至少一個部門已指派即計為在使用中；沒有任何部門指派計為未使用
    used = {r.knowledge_base_id for r in rows if r.can_read}
    active_count = sum(1 for k in kbs if k.knowledge_base_id in used)
    counts = {
        "total": len(kbs),
        "active": active_count,
        "inactive": len(kbs) - active_count,
        "unknown": 0,
    }
    return templates.TemplateResponse(
        request,
        "pages/knowledge/list.html",
        {
            "counts": counts,
            "rows": view_rows,
            "departments": _department_views(departments),
            "current_department_name": _department_name(departments, actor.department_id),
            "q": q,
            "department_id": department_id,
            "is_it_reviewer": actor.is_it,
            "qa_choices": _qa_choices(kbs, rows, departments, actor),
            "error": error,
        },
        status_code=status_code,
    )


def _list_error(request: Request, error: RagRuleError) -> HTMLResponse:
    not_found = _not_found(error)
    if not_found:
        raise not_found
    return _render_knowledge_list(request, error=error.message, status_code=400)


# ---- 知識庫審核與部門選用 ----


@router.post("/{kb_id}/review/approve", response_model=None)
def approve_knowledge_base_review(request: Request, module: Module, kb_id: str) -> Response:
    """資訊部核准知識庫。"""
    actor = _authorize_review(request, module)
    try:
        rag_service.approve_knowledge_base(_uuid(kb_id), UUID(actor.user_id), actor.is_it)
    except RagRuleError as error:
        return _list_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge", status_code=303)


@router.post("/{kb_id}/review/reject", response_model=None)
def reject_knowledge_base_review(
    request: Request, module: Module, kb_id: str, comment: Annotated[str, Form()] = ""
) -> Response:
    """資訊部退回知識庫（原因必填），同時取消所有部門對它的問答選用。"""
    actor = _authorize_review(request, module)
    try:
        rag_service.reject_knowledge_base(_uuid(kb_id), UUID(actor.user_id), actor.is_it, comment)
    except RagRuleError as error:
        return _list_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge", status_code=303)


@router.post("/{kb_id}/review/resubmit", response_model=None)
def resubmit_knowledge_base_review(request: Request, module: Module, kb_id: str) -> Response:
    """已退回的知識庫重新送審（送審者與部門取自目前身分）。"""
    actor = _authorize(request, module, Action.EDIT)
    kb_id = _uuid(kb_id)
    try:
        kb, rows = rag_service.load_knowledge_base(kb_id)
        if not can_manage_knowledge_base(kb, rows, actor):
            raise RagRuleError("NOT_MANAGER", "目前部門不能管理這個知識庫")
        rag_service.resubmit_knowledge_base(kb_id, UUID(actor.user_id), UUID(actor.department_id))
    except RagRuleError as error:
        return _list_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge", status_code=303)


@router.post("/qa-source", response_model=None)
def set_department_qa_source(
    request: Request,
    module: Module,
    department_id: Annotated[str, Form()],
    knowledge_base_id: Annotated[str, Form()] = "",
) -> Response:
    """設定部門問答選用的知識庫（每個部門一個；空白為取消選用）；只能設定目前部門，資訊部除外。"""
    actor = _authorize(request, module, Action.EDIT)
    department_id = _uuid(department_id)
    try:
        if knowledge_base_id:
            rag_service.set_qa_source(_uuid(knowledge_base_id), department_id, actor)
        else:
            rag_service.clear_qa_source(department_id, actor)
    except RagRuleError as error:
        return _list_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge", status_code=303)


@router.post("/{kb_id}/toggle-dept", response_class=HTMLResponse)
def toggle_kb_department(
    request: Request,
    module: Module,
    kb_id: str,
    department_id: Annotated[str, Form()],
    enable: Annotated[str, Form()],
) -> HTMLResponse:
    """切換知識庫的部門指派（HTMX 片段替換）。"""
    actor = _authorize(request, module, Action.EDIT)
    kb_id = _uuid(kb_id)
    try:
        rows = rag_service.set_department_assignment(
            kb_id, _uuid(department_id), enable.lower() == "true", actor
        )
        kb, _rows = rag_service.load_knowledge_base(kb_id)
    except RagRuleError as error:
        not_found = _not_found(error)
        if not_found:
            raise not_found from None
        return HTMLResponse(
            f'<p class="alert alert--danger">{escape(error.message)}</p>', status_code=400
        )
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return templates.TemplateResponse(
        request,
        "pages/knowledge/_dept_badges.html",
        {
            "badges": _kb_badges(kb, rows, _departments(), actor),
            "toggle_url": f"/knowledge/{kb_id}/toggle-dept",
            "target": f"dept-badges-{kb_id}",
        },
    )


# ---- 文件清單 ----


def _doc_badges(
    kb: KnowledgeBase,
    rag_file: RagFile,
    view: rag_service.DocumentsView,
    departments: Sequence[DepartmentOption],
    actor: RagActor,
) -> list[dict[str, Any]]:
    # 只有已指派此知識庫的部門才會列在文件的使用部門中
    assigned = {r.department_id for r in view.access_rows if r.can_read}
    using = {
        u.department_id
        for u in view.usage_rows
        if u.rag_file_id == rag_file.rag_file_id and u.use_document
    }
    manager = can_manage_knowledge_base(kb, view.access_rows, actor)
    return [
        {
            "id": d.department_id,
            "name": d.department_name,
            "state": "on" if d.department_id in using else "off",
            "can_toggle": manager or d.department_id == actor.department_id,
        }
        for d in departments
        if d.department_id in assigned
    ]


def _index_note(rag_file: RagFile, view: rag_service.DocumentsView) -> str | None:
    """最近一次索引失敗或等待重試的原因（固定代碼轉中文）。"""
    job = view.latest_jobs.get(rag_file.rag_file_id)
    if job is None or not job.error_message:
        return None
    if job.job_status != JOB_FAILED and rag_file.parse_status != PARSE_UPLOADED:
        return None
    return ERROR_LABELS.get(job.error_message, f"索引失敗（{job.error_message}）")


@router.get("/{kb_id}/documents", response_class=HTMLResponse)
def document_list(
    request: Request,
    kb_id: str,
    department_id: Annotated[str, Query()] = "",
    q: Annotated[str, Query()] = "",
    approval: Annotated[str, Query()] = "all",
) -> HTMLResponse:
    """知識庫文件清單：統計、搜尋、核准篩選、文件部門選用、索引、核准／暫停。"""
    return _render_document_list(
        request, _uuid(kb_id), department_id=department_id, q=q, approval=approval
    )


def _render_document_list(
    request: Request,
    kb_id: str,
    *,
    department_id: str = "",
    q: str = "",
    approval: str = "all",
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    actor = _viewer(request)
    departments = _departments()
    try:
        view = rag_service.load_documents(kb_id)
    except RagRuleError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None
    except RagUnavailableError as exc:
        raise _unavailable(exc) from None
    kb = view.knowledge_base
    manager = can_manage_knowledge_base(kb, view.access_rows, actor)

    files = list(view.files)
    if q.strip():
        term = q.strip().lower()
        files = [
            f
            for f in files
            if term in f.title.lower() or term in (f.original_filename or "").lower()
        ]
    if approval == "approved":
        files = [f for f in files if f.is_approved]
    elif approval == "unapproved":
        files = [f for f in files if not f.is_approved]

    rows = [
        {
            "id": f.rag_file_id,
            "title": f.title,
            "file_name": f.original_filename or "",
            "status": "active" if f.is_approved else "hold",
            "processing_status": f.parse_status,
            "processing_label": PARSE_LABELS.get(f.parse_status, "等待索引"),
            "index_note": _index_note(f, view),
            "can_approve": f.parse_status == PARSE_READY,
            "can_index": manager and f.parse_status in {PARSE_UPLOADED, PARSE_FAILED},
            "created_at": f.created_at,
            "badges": _doc_badges(kb, f, view, departments, actor),
            "toggle_url": f"/knowledge/{kb_id}/documents/{f.rag_file_id}/toggle-dept",
        }
        for f in files
    ]
    approved_count = sum(1 for f in view.files if f.is_approved)
    counts = {
        "total": len(view.files),
        "approved": approved_count,
        "unapproved": len(view.files) - approved_count,
    }
    assigned = [
        {"id": d.department_id, "name": d.department_name}
        for d in departments
        if any(r.department_id == d.department_id and r.can_read for r in view.access_rows)
    ]
    kb_view = {
        "id": kb_id,
        "title": kb.knowledge_name,
        "badges": _kb_badges(kb, view.access_rows, departments, actor),
        "toggle_url": f"/knowledge/{kb_id}/toggle-dept",
    }
    return templates.TemplateResponse(
        request,
        "pages/knowledge/documents.html",
        {
            "kb": kb_view,
            "department_id": department_id or actor.department_id,
            "assigned_departments": assigned,
            "departments": _department_views(departments),
            "current_department_name": _department_name(departments, actor.department_id),
            "counts": counts,
            "rows": rows,
            "q": q,
            "approval": approval,
            "is_it_reviewer": actor.is_it,
            "can_manage": manager,
            "error": error,
        },
        status_code=status_code,
    )


def _documents_error(request: Request, kb_id: str, error: RagRuleError) -> HTMLResponse:
    not_found = _not_found(error)
    if not_found:
        raise not_found
    return _render_document_list(request, kb_id, error=error.message, status_code=400)


@router.post("/{kb_id}/documents/{doc_id}/toggle-dept", response_class=HTMLResponse)
def toggle_document_department(
    request: Request,
    module: Module,
    kb_id: str,
    doc_id: str,
    department_id: Annotated[str, Form()],
    enable: Annotated[str, Form()],
) -> HTMLResponse:
    """切換文件在部門的選用（HTMX 片段替換）。"""
    actor = _authorize(request, module, Action.EDIT)
    kb_id, doc_id = _uuid(kb_id), _uuid(doc_id)
    try:
        view = rag_service.set_file_usage(
            kb_id, doc_id, _uuid(department_id), enable.lower() == "true", actor
        )
    except RagRuleError as error:
        not_found = _not_found(error)
        if not_found:
            raise not_found from None
        return HTMLResponse(
            f'<p class="alert alert--danger">{escape(error.message)}</p>', status_code=400
        )
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    rag_file = next((f for f in view.files if f.rag_file_id == doc_id), None)
    if rag_file is None:
        raise HTTPException(status_code=404, detail="找不到指定文件")
    return templates.TemplateResponse(
        request,
        "pages/knowledge/_dept_badges.html",
        {
            "badges": _doc_badges(view.knowledge_base, rag_file, view, _departments(), actor),
            "toggle_url": f"/knowledge/{kb_id}/documents/{doc_id}/toggle-dept",
            "target": f"dept-badges-doc-{doc_id}",
        },
    )


@router.post("/{kb_id}/documents/{doc_id}/status", response_model=None)
def update_document_status(
    request: Request,
    module: Module,
    kb_id: str,
    doc_id: str,
    status: Annotated[str, Form()],
) -> Response:
    """資訊部核准（active，文件須已索引）或暫停（hold）文件。"""
    actor = _authorize_review(request, module)
    kb_id, doc_id = _uuid(kb_id), _uuid(doc_id)
    try:
        rag_service.load_file(kb_id, doc_id)
        if status == "active":
            rag_service.approve_file(doc_id, UUID(actor.user_id), actor.is_it)
        else:
            rag_service.suspend_file(doc_id, actor.is_it)
    except RagRuleError as error:
        return _documents_error(request, kb_id, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse(f"/knowledge/{kb_id}/documents", status_code=303)


@router.post("/{kb_id}/documents/{doc_id}/index", response_model=None)
def index_document(request: Request, module: Module, kb_id: str, doc_id: str) -> Response:
    """手動索引一份文件（擷取文字、切段、向量），一次一份，不在背景執行。"""
    actor = _authorize(request, module, Action.EDIT)
    kb_id, doc_id = _uuid(kb_id), _uuid(doc_id)
    try:
        rag_service.index_document(kb_id, doc_id, actor)
    except RagRuleError as error:
        return _documents_error(request, kb_id, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse(f"/knowledge/{kb_id}/documents", status_code=303)


# ---- 編輯、刪除、內容與下載 ----


def _edit_page(
    request: Request,
    *,
    mode: str,
    kb: dict[str, Any],
    doc: dict[str, Any] | None,
    can_edit: bool,
    content: dict[str, Any] | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "pages/knowledge/edit.html",
        {
            "mode": mode,
            "kb": kb,
            "doc": doc,
            "can_edit": can_edit,
            "content": content,
            "error": error,
        },
        status_code=status_code,
    )


def _doc_view(rag_file: RagFile) -> dict[str, Any]:
    return {
        "id": rag_file.rag_file_id,
        "title": rag_file.title,
        "file_name": rag_file.original_filename or "",
        "revision": rag_file.file_version,
    }


@router.get("/{kb_id}/edit", response_class=HTMLResponse)
def edit_knowledge_base_page(request: Request, kb_id: str) -> HTMLResponse:
    """編輯知識庫名稱頁面。"""
    return _render_kb_edit(request, _uuid(kb_id))


def _render_kb_edit(
    request: Request, kb_id: str, *, error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    try:
        kb, rows = rag_service.load_knowledge_base(kb_id)
    except RagRuleError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None
    except RagUnavailableError as exc:
        raise _unavailable(exc) from None
    return _edit_page(
        request,
        mode="kb_name",
        kb={"id": kb_id, "title": kb.knowledge_name},
        doc=None,
        can_edit=can_manage_knowledge_base(kb, rows, _viewer(request)),
        error=error,
        status_code=status_code,
    )


@router.post("/{kb_id}/rename", response_model=None)
def rename_knowledge_base(
    request: Request, module: Module, kb_id: str, title: Annotated[str, Form()]
) -> Response:
    """重新命名知識庫（代碼與檔案資料夾不變）。"""
    actor = _authorize(request, module, Action.EDIT)
    kb_id = _uuid(kb_id)
    try:
        rag_service.rename_knowledge_base(kb_id, title, actor)
    except RagRuleError as error:
        not_found = _not_found(error)
        if not_found:
            raise not_found from None
        return _render_kb_edit(request, kb_id, error=error.message, status_code=400)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge", status_code=303)


@router.post("/{kb_id}/delete", response_model=None)
def delete_knowledge_base(request: Request, module: Module, kb_id: str) -> Response:
    """刪除知識庫及其所有文件。"""
    actor = _authorize(request, module, Action.DELETE)
    kb_id = _uuid(kb_id)
    try:
        rag_service.delete_knowledge_base(kb_id, actor)
    except RagRuleError as error:
        not_found = _not_found(error)
        if not_found:
            raise not_found from None
        return _render_kb_edit(request, kb_id, error=error.message, status_code=400)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge", status_code=303)


def _load_doc(
    kb_id: str, doc_id: str
) -> tuple[KnowledgeBase, tuple[DepartmentAccess, ...], RagFile]:
    try:
        return rag_service.load_file(kb_id, doc_id)
    except RagRuleError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from None
    except RagUnavailableError as exc:
        raise _unavailable(exc) from None


def _render_doc_edit(
    request: Request, kb_id: str, doc_id: str, *, error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    kb, rows, rag_file = _load_doc(kb_id, doc_id)
    return _edit_page(
        request,
        mode="doc_name",
        kb={"id": kb_id, "title": kb.knowledge_name},
        doc=_doc_view(rag_file),
        can_edit=can_manage_knowledge_base(kb, rows, _viewer(request)),
        error=error,
        status_code=status_code,
    )


@router.get("/{kb_id}/documents/{doc_id}/edit", response_class=HTMLResponse)
def edit_document_name_page(request: Request, kb_id: str, doc_id: str) -> HTMLResponse:
    """編輯文件名稱頁面。"""
    return _render_doc_edit(request, _uuid(kb_id), _uuid(doc_id))


@router.post("/{kb_id}/documents/{doc_id}/rename", response_model=None)
def rename_document(
    request: Request, module: Module, kb_id: str, doc_id: str, title: Annotated[str, Form()]
) -> Response:
    """重新命名文件。"""
    actor = _authorize(request, module, Action.EDIT)
    kb_id, doc_id = _uuid(kb_id), _uuid(doc_id)
    try:
        rag_service.rename_file(kb_id, doc_id, title, actor)
    except RagRuleError as error:
        not_found = _not_found(error)
        if not_found:
            raise not_found from None
        return _render_doc_edit(request, kb_id, doc_id, error=error.message, status_code=400)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse(f"/knowledge/{kb_id}/documents", status_code=303)


@router.post("/{kb_id}/documents/{doc_id}/delete", response_model=None)
def delete_document(request: Request, module: Module, kb_id: str, doc_id: str) -> Response:
    """刪除文件。"""
    actor = _authorize(request, module, Action.DELETE)
    kb_id, doc_id = _uuid(kb_id), _uuid(doc_id)
    try:
        rag_service.delete_file(kb_id, doc_id, actor)
    except RagRuleError as error:
        not_found = _not_found(error)
        if not_found:
            raise not_found from None
        return _render_doc_edit(request, kb_id, doc_id, error=error.message, status_code=400)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse(f"/knowledge/{kb_id}/documents", status_code=303)


def _extension(rag_file: RagFile) -> str:
    name = rag_file.original_filename or ""
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _render_content(
    request: Request, kb_id: str, doc_id: str, *, error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    kb, rows, rag_file = _load_doc(kb_id, doc_id)
    text: str | None = None
    editable = _extension(rag_file) in TEXT_EDITABLE_EXTENSIONS
    if editable:
        try:
            _file, raw = rag_service.read_file_content(kb_id, doc_id)
            text = raw.decode("utf-8-sig")
        except RagUnavailableError as exc:
            error = error or str(exc)
            editable = False
        except UnicodeDecodeError:
            editable = False
    return _edit_page(
        request,
        mode="doc_content",
        kb={"id": kb_id, "title": kb.knowledge_name},
        doc=_doc_view(rag_file),
        can_edit=can_manage_knowledge_base(kb, rows, _viewer(request)),
        content={"text": text, "text_editable": editable, "preview_url": None},
        error=error,
        status_code=status_code,
    )


@router.get("/{kb_id}/documents/{doc_id}/content", response_class=HTMLResponse)
def document_content_page(request: Request, kb_id: str, doc_id: str) -> HTMLResponse:
    """檢視與編輯文件內容（TXT／Markdown 可直接改文字，其他格式可替換原檔）。"""
    return _render_content(request, _uuid(kb_id), _uuid(doc_id))


@router.post("/{kb_id}/documents/{doc_id}/content", response_model=None)
async def update_document_content(
    request: Request,
    module: Module,
    kb_id: str,
    doc_id: str,
    text: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    """更新文件文字內容或上傳替換檔案；內容變更後撤銷核准並重新等待索引。"""
    actor = _authorize(request, module, Action.EDIT)
    kb_id, doc_id = _uuid(kb_id), _uuid(doc_id)
    _kb, _rows, rag_file = _load_doc(kb_id, doc_id)
    if file is not None and file.filename:
        filename, content = file.filename, await file.read()
    elif text is not None and _extension(rag_file) in TEXT_EDITABLE_EXTENSIONS:
        filename, content = rag_file.original_filename or "", text.encode("utf-8")
    else:
        return _render_content(
            request, kb_id, doc_id, error="請修改文字或選擇替換檔案", status_code=400
        )
    try:
        rag_service.replace_file_content(kb_id, doc_id, filename, content, actor)
    except RagRuleError as error:
        not_found = _not_found(error)
        if not_found:
            raise not_found from None
        return _render_content(request, kb_id, doc_id, error=error.message, status_code=400)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse(f"/knowledge/{kb_id}/documents/{doc_id}/content", status_code=303)


@router.get("/{kb_id}/documents/{doc_id}/download")
def download_document(kb_id: str, doc_id: str) -> Response:
    """下載原始文件。"""
    try:
        rag_file, content = rag_service.read_file_content(_uuid(kb_id), _uuid(doc_id))
    except RagRuleError as error:
        raise HTTPException(status_code=404, detail=error.message) from None
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    filename = quote(rag_file.original_filename or "document")
    return Response(
        content=content,
        media_type=rag_file.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


# ---- 上傳 ----


def _upload_result(lines: Sequence[tuple[bool, str]]) -> HTMLResponse:
    ok = all(success for success, _ in lines)
    items = "<br>".join(f"{'✓' if success else '✗'} {escape(text)}" for success, text in lines)
    return HTMLResponse(
        f'<div class="alert {"alert--success" if ok else "alert--warning"}" style="margin-top:12px">'
        f"{items}</div>"
        '<p class="muted small-text">請重整頁面檢視最新文件列表；文件需索引並經資訊部核准後才會用於問答。</p>'
    )


@router.post("/documents", response_class=HTMLResponse)
async def upload_documents(
    request: Request,
    module: Module,
    files: Annotated[list[UploadFile], File()],
    knowledge_base_id: Annotated[str | None, Form()] = None,
    knowledge_base_title: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """批次上傳文件到既有或新建立的知識庫（HTMX 回傳結果片段）；上傳部門為目前部門。"""
    actor = _authorize(request, module, Action.CREATE)
    named = [f for f in files if f.filename]
    items = [
        UploadItem(
            f.filename or "",
            await f.read(),
            title.strip() if title and title.strip() and len(named) == 1 else None,
        )
        for f in named
    ]
    try:
        if knowledge_base_id:
            kb_id = _uuid(knowledge_base_id)
        else:
            kb_id = rag_service.create_knowledge_base(
                knowledge_base_title or "", actor
            ).knowledge_base_id
        outcomes = rag_service.upload_documents(kb_id, items, actor)
    except RagRuleError as error:
        return HTMLResponse(
            f'<div class="alert alert--danger">{escape(error.message)}</div>', status_code=400
        )
    except RagUnavailableError as error:
        return HTMLResponse(
            f'<div class="alert alert--danger">{escape(str(error))}</div>', status_code=503
        )
    return _upload_result([(o.ok, f"{o.filename}：{o.message}") for o in outcomes])
