"""模組名稱：app.services.department_qa_service；功能說明：部門問答選項與勾選驗權。"""

from typing import Any
from uuid import UUID

from app.db.repositories import rag_repository as repository
from app.schemas.e_admin import UnavailableError
from app.schemas.rag import RagActor, RagRuleError
from app.services import rag_qa_service as qa


def configuration_status(actor: RagActor) -> dict[str, Any]:
    """診斷空清單原因；只讀本人目前部門，不更動指派或自動放寬搜尋。"""
    db = repository.open_session()
    try:
        rows = repository.qa_department_status(db, UUID(actor.user_id), UUID(actor.department_id))
    except repository.SQLAlchemyError:
        raise UnavailableError from None
    finally:
        db.close()
    selected = [row for row in rows if row["selected"]]
    unavailable = [row["title"] for row in selected if not row["eligible"]]
    return {
        "readable_count": len(rows),
        "selected_count": len(selected),
        "available_count": sum(row["eligible"] for row in selected),
        "unavailable": unavailable,
    }


def choices(actor: RagActor) -> list[dict[str, str]]:
    """只讀取本人目前部門可供問答的知識庫。"""
    db = repository.open_session()
    try:
        return repository.list_qa_choices(db, UUID(actor.user_id), UUID(actor.department_id))
    except repository.SQLAlchemyError:
        raise UnavailableError from None
    finally:
        db.close()


def ask(question: str, actor: RagActor) -> qa.QaAnswer:
    """每次提問自動讀取目前部門最新問答來源，不採信用戶端的知識庫清單。"""
    if not choices(actor):
        raise RagRuleError("SOURCES_REQUIRED", "目前部門尚無可用的問答資料，請聯絡管理人員。")
    return qa.ask(question, actor)
