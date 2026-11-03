"""
模組名稱：app.routers.knowledge
功能說明：知識庫與文件管理——列表、文件清單、上傳對話框、部門指派與問答選用、審核、索引、編輯與下載，
          以及問答測試（/knowledge/chat）。
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
    EFFECTIVE_DATE_LABELS,
    ERROR_LABELS,
    JOB_FAILED,
    PARSE_LABELS,
    PARSE_UPLOADED,
    SOURCE_TYPE_LABELS,
    TEXT_EDITABLE_EXTENSIONS,
    DepartmentAccess,
    DepartmentOption,
    KnowledgeBase,
    RagActor,
    RagFile,
    RagRuleError,
)
from app.services import rag_admin_service as rag_service
from app.services import rag_qa_service
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
    try:
        module.check(identity, KNOWLEDGE, Action.DELETE, Target(identity.selected_department_id))
        can_delete = True
    except DeniedError:
        can_delete = False
    # 所有驗權與顯示資料都成功取得後，才一次發佈本次請求的登入狀態。
    request.state.e_configuration_authenticated = True
    request.state.e_configuration_menu = menu
    request.state.header_departments = departments
    request.state.header_selected_department_id = identity.selected_department_id
    request.state.knowledge_identity = identity
    request.state.is_it_workspace = is_it_workspace
    request.state.knowledge_can_delete = can_delete


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
            "owner": d.department_id == kb.submitted_department_id,
        }
        for d in departments
    ]


def _qa_choices(
    kbs: Sequence[KnowledgeBase],
    rows: Sequence[DepartmentAccess],
    departments: Sequence[DepartmentOption],
    actor: RagActor,
) -> list[dict[str, Any]]:
    """每個部門可選用問答的知識庫（已核准、啟用、非空且已開放）與目前選用。"""
    choices = []
    for dept in departments:
        mine = [r for r in rows if r.department_id == dept.department_id]
        readable = {r.knowledge_base_id for r in mine if r.can_read}
        options = [
            {"id": kb.knowledge_base_id, "title": kb.knowledge_name}
            for kb in kbs
            if kb.approval_status == APPROVAL_APPROVED
            and kb.is_active
            and kb.file_count >= 1
            and kb.knowledge_base_id in readable
        ]
        selected = {r.knowledge_base_id for r in mine if r.use_for_qa}
        current = [option["id"] for option in options if option["id"] in selected]
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
                "approval_label": APPROVAL_LABELS.get(kb.approval_status, "待審查"),
                "review_comment": kb.review_comment,
                "qa_department_names": [
                    _department_name(departments, r.department_id) for r in mine if r.use_for_qa
                ],
                "can_manage": can_manage_knowledge_base(kb, mine, actor),
                "to_index": len(overview.files_to_index.get(kb.knowledge_base_id, [])),
                "file_count": kb.file_count,
            }
        )

    # 至少一個部門已指派（use_for_qa）即計為在使用中；只開放未指派不算使用
    used = {r.knowledge_base_id for r in rows if r.use_for_qa}
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


# ---- 獨立檔案庫（必須先於 /{kb_id} 路由）----


def _load_doc(file_id: str) -> RagFile:
    try:
        return rag_service.load_file(file_id)
    except RagRuleError as error:
        raise HTTPException(status_code=404, detail=error.message) from None
    except RagUnavailableError as error:
        raise _unavailable(error) from None


def _upload_error(error: RagRuleError) -> HTMLResponse:
    """上傳對話框以 HTMX 顯示片段；其他檔案操作是一般表單，回完整頁面。"""
    not_found = _not_found(error)
    if not_found:
        raise not_found
    return HTMLResponse(
        f'<p class="alert alert--danger">{escape(error.message)}</p>', status_code=400
    )


def _library_error(request: Request, error: RagRuleError) -> HTMLResponse:
    not_found = _not_found(error)
    if not_found:
        raise not_found
    return _render_file_library(request, error=error.message, status_code=400)


def _file_content_error(request: Request, file_id: str, error: RagRuleError) -> HTMLResponse:
    not_found = _not_found(error)
    if not_found:
        raise not_found
    return _render_file_content(request, file_id, error=error.message, status_code=400)


def _render_file_library(
    request: Request,
    q: str = "",
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    try:
        view = rag_service.load_file_library()
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    departments = _departments()
    actor = _viewer(request)
    rows = []
    for f in view.files:
        if (
            q.strip()
            and q.strip().casefold() not in (f.title + " " + (f.original_filename or "")).casefold()
        ):
            continue
        rows.append(
            {
                "file": f,
                "source_label": SOURCE_TYPE_LABELS.get(f.source_type, "未分類"),
                "date_label": f.effective_date.isoformat()
                if f.effective_date
                else EFFECTIVE_DATE_LABELS.get(f.effective_date_status, "未知"),
                "parse_label": PARSE_LABELS.get(f.parse_status, "等待索引"),
                "department": _department_name(departments, f.owner_department_id or ""),
                "references": view.references.get(f.rag_file_id, ()),
                "can_edit": rag_service.can_manage_file(f, actor),
                "index_note": _index_note(f, view),
            }
        )
    return templates.TemplateResponse(
        request,
        "pages/knowledge/files.html",
        {
            "rows": rows,
            "q": q,
            "is_it_reviewer": actor.is_it,
            "current_department_name": _department_name(departments, actor.department_id),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/files", response_class=HTMLResponse)
def file_library(request: Request, q: Annotated[str, Query()] = "") -> HTMLResponse:
    """全部門檔案清單與搜尋，含待審查檔案。"""
    return _render_file_library(request, q)


@router.post("/files/upload", response_class=HTMLResponse)
async def upload_documents(
    request: Request,
    module: Module,
    files: Annotated[list[UploadFile], File()],
    source_type: Annotated[str, Form()],
    effective_date_status: Annotated[str, Form()],
    effective_date: Annotated[str | None, Form()] = None,
    source_note: Annotated[str | None, Form()] = None,
    knowledge_base_id: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """上傳獨立檔案；可同交易加入指定知識庫，上傳部門不接受表單自報。"""
    actor = _authorize(request, module, Action.CREATE)
    named = [f for f in files if f.filename]
    if len(named) != 1:
        return HTMLResponse(
            '<p class="alert alert--danger">一次上傳一份文件，請逐份上傳。</p>', status_code=400
        )
    item = UploadItem(
        named[0].filename or "",
        await named[0].read(),
        title,
        source_type,
        effective_date_status,
        effective_date,
        source_note,
    )
    try:
        results = rag_service.upload_documents(
            _uuid(knowledge_base_id) if knowledge_base_id else None, [item], actor
        )
    except RagRuleError as error:
        return _upload_error(error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    result = _upload_result([(o.ok, f"{o.filename}：{o.message}") for o in results])
    if not all(o.ok for o in results):
        result.status_code = 400
    return result


def _extension(rag_file: RagFile) -> str:
    return (rag_file.original_filename or "").rsplit(".", 1)[-1].lower()


def _render_file_content(
    request: Request,
    file_id: str,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    rag_file = _load_doc(file_id)
    editable = _extension(rag_file) in TEXT_EDITABLE_EXTENSIONS
    text = None
    if editable:
        try:
            _file, raw = rag_service.read_file_content(file_id)
            text = raw.decode("utf-8-sig")
        except (UnicodeDecodeError, RagUnavailableError):
            editable = False
    content = {"text": text, "text_editable": editable, "preview_url": None}
    return _edit_page(
        request,
        mode="doc_content",
        kb={},
        doc=_doc_view(rag_file),
        can_edit=rag_service.can_manage_file(rag_file, _viewer(request)),
        content=content,
        error=error,
        status_code=status_code,
    )


@router.post("/files/{file_id}/rename", response_model=None)
def rename_document(
    request: Request, module: Module, file_id: str, title: Annotated[str, Form()]
) -> Response:
    """上傳部門或資訊部修改檔案名稱。"""
    actor = _authorize(request, module, Action.EDIT)
    file_id = _uuid(file_id)
    try:
        rag_service.rename_file(file_id, title, actor)
    except RagRuleError as error:
        return _library_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge/files", status_code=303)


@router.post("/files/{file_id}/delete", response_model=None)
def delete_document(request: Request, module: Module, file_id: str) -> Response:
    """無任何引用時才刪檔案；不符合時顯示引用名稱。"""
    actor = _authorize(request, module, Action.DELETE)
    file_id = _uuid(file_id)
    try:
        rag_service.delete_file(file_id, actor)
    except RagRuleError as error:
        return _library_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge/files", status_code=303)


@router.get("/files/{file_id}/content", response_class=HTMLResponse)
def document_content_page(request: Request, file_id: str) -> HTMLResponse:
    """檢視原檔與編輯文字。"""
    return _render_file_content(request, _uuid(file_id))


@router.post("/files/{file_id}/content", response_model=None)
async def update_document_content(
    request: Request,
    module: Module,
    file_id: str,
    text: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> Response:
    """替換檔案內容；已核准知識庫引用時由服務拒絕。"""
    actor = _authorize(request, module, Action.EDIT)
    file_id = _uuid(file_id)
    current = _load_doc(file_id)
    if file is not None and file.filename:
        filename, raw = file.filename, await file.read()
    elif text is not None and _extension(current) in TEXT_EDITABLE_EXTENSIONS:
        filename, raw = current.original_filename or "", text.encode("utf-8")
    else:
        return _render_file_content(
            request,
            file_id,
            error="請修改文字或選擇替換檔案",
            status_code=400,
        )
    try:
        rag_service.replace_file_content(file_id, filename, raw, actor)
    except RagRuleError as error:
        return _file_content_error(request, file_id, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse(f"/knowledge/files/{file_id}/content", status_code=303)


@router.get("/files/{file_id}/download")
def download_document(file_id: str) -> Response:
    """依資料表記錄的原路徑下載檔案。"""
    try:
        rag_file, raw = rag_service.read_file_content(_uuid(file_id))
    except RagRuleError as error:
        raise HTTPException(status_code=404, detail=error.message) from None
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    filename = quote(rag_file.original_filename or "document")
    return Response(
        content=raw,
        media_type=rag_file.mime_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.post("/files/{file_id}/status", response_model=None)
def update_document_status(
    request: Request, module: Module, file_id: str, status: Annotated[str, Form()]
) -> Response:
    """資訊部核准或暫停獨立檔案。"""
    actor = _authorize_review(request, module)
    file_id = _uuid(file_id)
    try:
        if status == "active":
            rag_service.approve_file(file_id, UUID(actor.user_id), True)
        elif status == "hold":
            rag_service.suspend_file(file_id, True)
        else:
            raise RagRuleError("BAD_STATUS", "未知的審核操作")
    except RagRuleError as error:
        return _library_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge/files", status_code=303)


@router.post("/files/{file_id}/index", response_model=None)
def index_document(request: Request, module: Module, file_id: str) -> Response:
    """資訊部建立檔案索引；同一檔案供所有關聯知識庫共用。"""
    actor = _authorize_review(request, module)
    try:
        rag_service.index_document(_uuid(file_id), actor)
    except RagRuleError as error:
        return _library_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge/files", status_code=303)


@router.post("/create", response_model=None)
def create_knowledge_base(
    request: Request, module: Module, title: Annotated[str, Form()]
) -> Response:
    """建立待審查空知識庫，之後由檔案庫選檔。"""
    actor = _authorize(request, module, Action.CREATE)
    try:
        kb = rag_service.create_knowledge_base(title, actor)
    except RagRuleError as error:
        return _list_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse(f"/knowledge/{kb.knowledge_base_id}/documents", status_code=303)


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
    knowledge_base_id: Annotated[list[str] | None, Form()] = None,
) -> Response:
    """整組設定部門問答知識庫；未勾選全部取消，只能設定目前部門，資訊部除外。"""
    actor = _authorize(request, module, Action.EDIT)
    department_id = _uuid(department_id)
    selected_ids = [_uuid(value) for value in knowledge_base_id or ()]
    try:
        rag_service.set_qa_sources(selected_ids, department_id, actor)
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
    """切換知識庫的開放部門（HTMX 片段替換）。"""
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


def _index_note(
    rag_file: RagFile, view: rag_service.DocumentsView | rag_service.FileLibrary
) -> str | None:
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
    q: Annotated[str, Query()] = "",
    approval: Annotated[str, Query()] = "all",
) -> HTMLResponse:
    """知識庫的檔案：統計、搜尋、核准篩選、加入與移除關聯。"""
    return _render_document_list(request, _uuid(kb_id), q=q, approval=approval)


def _render_document_list(
    request: Request,
    kb_id: str,
    *,
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
            "processing_label": PARSE_LABELS.get(f.parse_status, "等待索引"),
            "index_note": _index_note(f, view),
        }
        for f in files
    ]
    approved_count = sum(1 for f in view.files if f.is_approved)
    counts = {
        "total": len(view.files),
        "approved": approved_count,
        "unapproved": len(view.files) - approved_count,
    }
    kb_view = {
        "id": kb_id,
        "title": kb.knowledge_name,
        "file_count": kb.file_count,
        "badges": _kb_badges(kb, view.access_rows, departments, actor),
        "toggle_url": f"/knowledge/{kb_id}/toggle-dept",
    }
    return templates.TemplateResponse(
        request,
        "pages/knowledge/documents.html",
        {
            "kb": kb_view,
            "current_department_name": _department_name(departments, actor.department_id),
            "counts": counts,
            "rows": rows,
            "q": q,
            "approval": approval,
            "can_manage": manager,
            "available_files": [
                f
                for f in view.available_files
                if f.rag_file_id not in {x.rag_file_id for x in view.files}
            ],
            "error": error,
        },
        status_code=status_code,
    )


def _documents_error(request: Request, kb_id: str, error: RagRuleError) -> HTMLResponse:
    not_found = _not_found(error)
    if not_found:
        raise not_found
    return _render_document_list(request, kb_id, error=error.message, status_code=400)


@router.post("/{kb_id}/index-next", response_model=None)
def index_next_document(request: Request, module: Module, kb_id: str) -> Response:
    """資訊部在知識庫列表直接建立索引：處理此知識庫最早一份待索引的文件（一次一份）。"""
    _authorize_review(request, module)
    kb_id = _uuid(kb_id)
    try:
        pending = rag_service.load_overview().files_to_index.get(kb_id, [])
        if not pending:
            raise RagRuleError("NOTHING_TO_INDEX", "這個知識庫沒有待建立索引的文件")
        rag_service.index_document(pending[0], _viewer(request))
    except RagRuleError as error:
        return _list_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge", status_code=303)


@router.post("/{kb_id}/reindex", response_model=None)
def queue_knowledge_base_reindex(request: Request, module: Module, kb_id: str) -> Response:
    """資訊部把知識庫內已索引的文件重新排入索引，之後在列表逐份按「建立索引」。"""
    actor = _authorize_review(request, module)
    try:
        rag_service.queue_reindex(_uuid(kb_id), actor)
    except RagRuleError as error:
        return _list_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge", status_code=303)


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
    """只刪除知識庫與關聯，保留共用檔案。"""
    actor = _authorize(request, module, Action.DELETE)
    kb_id = _uuid(kb_id)
    try:
        rag_service.delete_knowledge_base(kb_id, actor)
    except RagRuleError as error:
        return _list_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse("/knowledge", status_code=303)


# ---- 上傳 ----


def _upload_result(lines: Sequence[tuple[bool, str]]) -> HTMLResponse:
    ok = all(success for success, _ in lines)
    items = "<br>".join(f"{'✓' if success else '✗'} {escape(text)}" for success, text in lines)
    return HTMLResponse(
        f'<div class="alert {"alert--success" if ok else "alert--warning"}" style="margin-top:12px">'
        f"{items}</div>"
        '<p class="muted small-text">請重整頁面檢視最新文件列表；文件需索引並經資訊部核准後才會用於問答。</p>'
    )


# ---- 問答 ----


def _chat_actor(request: Request) -> RagActor:
    """知識庫問答僅限已驗證身分的目前部門為資訊部。"""
    actor = _viewer(request)
    if not actor.is_it:
        raise DeniedError
    return actor


def _chat_choices(
    overview: rag_service.KnowledgeOverview,
    department_id: str | None = None,
    selected_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """可勾選的知識庫：已指派／未指派跟著所選部門；不選部門時沒有指派可言，列出全部。"""
    available = sorted(
        (
            kb
            for kb in overview.knowledge_bases
            if kb.approval_status == APPROVAL_APPROVED and kb.is_active and kb.file_count >= 1
        ),
        key=lambda kb: (kb.knowledge_name, kb.knowledge_base_id),
    )

    def group(title: str, kbs: Sequence[KnowledgeBase]) -> dict[str, Any]:
        options = [
            {
                "id": kb.knowledge_base_id,
                "title": kb.knowledge_name,
                "checked": kb.knowledge_base_id in selected_ids,
            }
            for kb in kbs
        ]
        return {"title": title, "options": options}

    if department_id is None:
        return {"groups": [group("全部知識庫", available)], "error": None}
    assigned = {
        row.knowledge_base_id
        for row in overview.access_rows
        if row.department_id == department_id and row.use_for_qa
    }
    return {
        "groups": [
            group("已指派", [kb for kb in available if kb.knowledge_base_id in assigned]),
            group("未指派", [kb for kb in available if kb.knowledge_base_id not in assigned]),
        ],
        "error": None,
    }


def _chat_overview() -> rag_service.KnowledgeOverview:
    try:
        return rag_service.load_overview()
    except RagUnavailableError as error:
        raise _unavailable(error) from None


@router.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request) -> HTMLResponse:
    """資訊部問答頁：切換部門與知識庫，再以清除對話自行比較；沒有一般部門聊天入口。

    打開時預選登入者目前的部門，並勾選該部門指派（use_for_qa）的知識庫，可直接提問。
    """
    actor = _chat_actor(request)
    departments = _departments()
    overview = _chat_overview()
    dept_id = (
        actor.department_id
        if actor.department_id in {d.department_id for d in departments}
        else None
    )
    assigned = [
        row.knowledge_base_id
        for row in overview.access_rows
        if dept_id and row.department_id == dept_id and row.use_for_qa
    ]
    return templates.TemplateResponse(
        request,
        "pages/knowledge/chat.html",
        {
            "departments": _department_views(departments),
            "selected_department_id": dept_id,
            **_chat_choices(overview, dept_id, assigned),
        },
    )


@router.get("/chat/choices", response_class=HTMLResponse)
def chat_choices(
    request: Request,
    department_id: Annotated[str, Query()] = "",
    knowledge_base_id: Annotated[list[str] | None, Query()] = None,
) -> HTMLResponse:
    """切換部門後重新分組已指派／未指派，保留勾選，不寫入指派。"""
    _chat_actor(request)
    dept_id = _uuid(department_id) if department_id else None
    selected = [_uuid(value) for value in knowledge_base_id or ()]
    if dept_id and dept_id not in {d.department_id for d in _departments()}:
        return templates.TemplateResponse(
            request,
            "pages/knowledge/_chat_choices.html",
            {"groups": [], "error": "部門不存在或已停用"},
            status_code=400,
        )
    return templates.TemplateResponse(
        request,
        "pages/knowledge/_chat_choices.html",
        _chat_choices(_chat_overview(), dept_id, selected),
    )


@router.post("/chat/ask", response_class=HTMLResponse)
def chat_ask(
    request: Request,
    question: Annotated[str, Form()] = "",
    knowledge_base_id: Annotated[list[str] | None, Form()] = None,
    department_id: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """依目前勾選回答一題；不保存，也不傳送前文。"""
    actor = _chat_actor(request)
    ids = [_uuid(value) for value in knowledge_base_id or ()]
    dept_id = _uuid(department_id) if department_id else None
    try:
        answer = rag_qa_service.ask_test(question, actor, ids, dept_id)
    except RagRuleError as error:
        return HTMLResponse(
            f'<div class="message assistant">{escape(error.message)}</div>', status_code=400
        )
    # 服務失敗或未通過檢查回 400：訊息照常顯示，畫面保留問題方便重試；找不到相關規定是正常回答，回 200
    return templates.TemplateResponse(
        request,
        "pages/knowledge/_chat_message.html",
        {"question": question.strip(), "answer": answer},
        status_code=400 if answer.failed else 200,
    )


@router.post("/{kb_id}/copy", response_model=None)
def copy_knowledge_base(
    request: Request, module: Module, kb_id: str, title: Annotated[str, Form()]
) -> Response:
    """複製檔案組合成新的待審查知識庫。"""
    actor = _authorize(request, module, Action.CREATE)
    try:
        copied = rag_service.copy_knowledge_base(_uuid(kb_id), title, actor)
    except RagRuleError as error:
        return _list_error(request, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse(f"/knowledge/{copied.knowledge_base_id}/documents", status_code=303)


@router.post("/{kb_id}/files", response_model=None)
def add_knowledge_files(
    request: Request,
    module: Module,
    kb_id: str,
    file_ids: Annotated[list[str] | None, Form()] = None,
) -> Response:
    """加入其他部門已核准檔案，全部 id 都先驗 UUID。"""
    actor = _authorize(request, module, Action.EDIT)
    kb_id = _uuid(kb_id)
    selected = [_uuid(value) for value in file_ids or ()]
    try:
        if not selected:
            raise RagRuleError("FILES_REQUIRED", "請勾選要加入的檔案")
        rag_service.add_files(kb_id, selected, actor)
    except RagRuleError as error:
        return _documents_error(request, kb_id, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse(f"/knowledge/{kb_id}/documents", status_code=303)


@router.post("/{kb_id}/files/{file_id}/remove", response_model=None)
def remove_knowledge_file(
    request: Request,
    module: Module,
    kb_id: str,
    file_id: str,
    expected_count: Annotated[int, Form()],
) -> Response:
    """確認後移除關聯，服務重新核對檔案數避免未確認的最後一份移除。"""
    actor = _authorize(request, module, Action.EDIT)
    kb_id, file_id = _uuid(kb_id), _uuid(file_id)
    try:
        rag_service.remove_file(kb_id, file_id, actor, expected_count)
    except RagRuleError as error:
        return _documents_error(request, kb_id, error)
    except RagUnavailableError as error:
        raise _unavailable(error) from None
    return RedirectResponse(f"/knowledge/{kb_id}/documents", status_code=303)
