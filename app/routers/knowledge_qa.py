"""
模組名稱：app.routers.knowledge_qa
功能說明：各部門獨立的 RAG 問答入口，自動使用管理端指派的部門問答來源。
路由：GET /knowledge-qa、POST /knowledge-qa/ask。
注意事項：不使用資訊部測試權限；身分由網站 Session 取得，每次請求驗權。
"""

from html import escape
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.core.templating import templates
from app.routers.e_admin import Module, require_e_origin, session_evidence
from app.schemas.e_admin import Action, DeniedError, Target
from app.schemas.rag import RagActor, RagRuleError
from app.services import department_qa_service as service
from app.services.e_admin_service import KNOWLEDGE_QA

QA_CAPABILITY = KNOWLEDGE_QA


def require_qa_access(request: Request, module: Module) -> None:
    """以目前部門問答查看權限驗證；不要求知識庫管理權限。"""
    if request.method != "GET":
        require_e_origin(request)
    identity = module.identity(session_evidence(request))
    module.check(identity, QA_CAPABILITY, Action.VIEW, Target(identity.selected_department_id))
    departments = module.departments(identity)
    menu = module.sidebar_menu(identity)
    department = next(
        (item for item in departments if item.department_id == identity.selected_department_id),
        None,
    )
    if department is None:
        raise DeniedError
    request.state.qa_actor = RagActor(str(identity.user_id), str(identity.selected_department_id))
    request.state.qa_department_name = department.department_name
    request.state.header_departments = departments
    request.state.header_selected_department_id = identity.selected_department_id
    request.state.e_configuration_menu = menu
    request.state.e_configuration_authenticated = True


router = APIRouter(tags=["knowledge-qa"], dependencies=[Depends(require_qa_access)])


@router.get("/knowledge-qa", response_class=HTMLResponse)
def chat_page(request: Request) -> HTMLResponse:
    """顯示對話區；來源由管理端配置，不將清單傳入樣板。"""
    has_sources = bool(service.choices(request.state.qa_actor))
    response = templates.TemplateResponse(
        request,
        "pages/knowledge_qa/chat.html",
        {
            "has_sources": has_sources,
            "department_id": request.state.qa_actor.department_id,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/knowledge-qa/ask", response_class=HTMLResponse)
def chat_ask(
    request: Request,
    question: Annotated[str, Form()] = "",
    context_department_id: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """每題依 Session 部門的最新來源回答；拒絕切換部門後的舊頁面。"""
    actor = request.state.qa_actor
    if context_department_id != actor.department_id:
        return HTMLResponse(
            "部門已切換，請重新整理後再提問",
            status_code=409,
            headers={"HX-Refresh": "true", "Cache-Control": "no-store"},
        )
    try:
        answer = service.ask(question, actor)
    except RagRuleError as error:
        return HTMLResponse(
            f'<div class="message assistant">{escape(error.message)}</div>', status_code=400
        )
    response = templates.TemplateResponse(
        request,
        "pages/knowledge_qa/_message.html",
        {
            "question": question.strip(),
            "answer": answer,
        },
        status_code=400 if answer.failed else 200,
    )
    response.headers["Cache-Control"] = "no-store"
    return response
