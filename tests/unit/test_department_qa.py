"""模組名稱：tests.unit.test_department_qa；功能說明：部門問答授權與檢索縮限離線測試。"""

import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.constants import SESSION_COOKIE_NAME
from app.db.repositories import rag_repository as repo
from app.routers import knowledge_qa as router
from app.schemas.e_access import EffectiveDepartment
from app.schemas.e_admin import Action, DeniedError, UnauthenticatedError
from app.schemas.rag import RagActor, RagRuleError
from app.services import department_qa_service as service
from app.services.e_admin_service import get_e_module
from app.services.rag_qa_service import QaAnswer

USER = UUID("00000000-0000-4000-8000-000000000001")
DEPT = UUID("00000000-0000-4000-8000-000000000002")
KB = "00000000-0000-4000-8000-000000000003"
OTHER = "00000000-0000-4000-8000-000000000004"
ACTOR = RagActor(str(USER), str(DEPT))


def test_finance_eight_department_sources_need_no_function_assignment() -> None:
    """問答沿用既有部門配置；資料庫不需存在額外功能指派表才可查詢。"""
    sql = (
        str(repo.LIST_QA_CHOICES)
        .replace("::text", "")
        .replace("CAST(:user_id AS uuid)", ":user_id")
        .replace("CAST(:department_id AS uuid)", ":department_id")
    )
    with sqlite3.connect(":memory:") as db:
        db.execute("ATTACH DATABASE ':memory:' AS public")
        db.execute(
            "CREATE TABLE public.v_user_department_rag_access (user_id TEXT, department_id TEXT, knowledge_base_id TEXT, can_read INT, use_for_qa INT)"
        )
        db.execute(
            "CREATE TABLE public.rag_knowledge_bases (knowledge_base_id TEXT, knowledge_name TEXT, approval_status TEXT, is_active INT, file_count INT)"
        )
        for i in range(8):
            key = str(i)
            db.execute(
                "INSERT INTO public.rag_knowledge_bases VALUES (?, ?, 'approved', 1, 1)",
                (key, f"財務{i}"),
            )
            db.execute(
                "INSERT INTO public.v_user_department_rag_access VALUES (?, ?, ?, 1, 1)",
                (str(USER), str(DEPT), key),
            )
        for key, dept, readable, selected in [
            ("foreign", OTHER, 1, 1),
            ("read-only", str(DEPT), 1, 0),
            ("unreadable", str(DEPT), 0, 1),
        ]:
            db.execute(
                "INSERT INTO public.rag_knowledge_bases VALUES (?, ?, 'approved', 1, 1)", (key, key)
            )
            db.execute(
                "INSERT INTO public.v_user_department_rag_access VALUES (?, ?, ?, ?, ?)",
                (str(USER), dept, key, readable, selected),
            )
        params = {"user_id": str(USER), "department_id": str(DEPT)}
        assert len(db.execute(sql, params).fetchall()) == 8
        db.execute(
            "UPDATE public.v_user_department_rag_access SET use_for_qa = 0 WHERE knowledge_base_id = '0'"
        )
        assert len(db.execute(sql, params).fetchall()) == 7


@pytest.mark.parametrize(
    "statement", [repo.LIST_QA_CHOICES, repo.SEARCH_CHUNKS, repo.SEARCH_KEYWORDS]
)
def test_qa_scope_has_no_extra_function_grant_requirement(statement) -> None:
    assert "rag_function_knowledge_bases" not in str(statement)
    assert "use_for_qa" in str(statement) and "can_read" in str(statement)


def test_extra_settings_route_is_not_registered() -> None:
    from app.main import app

    assert not any(getattr(route, "path", "") == "/knowledge/qa-settings" for route in app.routes)


def test_qa_capability_maps_to_confirmed_catalog_code() -> None:
    from app.services.admin_login_service import CATALOG_CODES
    from app.services.e_registry import capability, validate_registry

    registry = get_e_module().registry
    validate_registry(registry)
    definition = capability("rag.qa", registry)
    assert CATALOG_CODES["rag.qa"] == "rag_qa"
    assert definition.route == "/knowledge-qa"
    assert definition.actions == frozenset({Action.VIEW})
    assert not definition.system_only and not definition.management


def test_no_department_sources_rejects_before_model(monkeypatch) -> None:
    monkeypatch.setattr(service, "choices", lambda actor: [])
    model = MagicMock()
    monkeypatch.setattr(service.qa, "ask", model)
    with pytest.raises(RagRuleError):
        service.ask("問題", ACTOR)
    model.assert_not_called()


def test_selection_is_per_question_and_revocation_is_rechecked(monkeypatch) -> None:
    allowed = [{"id": KB, "title": "本部門"}]
    monkeypatch.setattr(service, "choices", lambda actor: allowed)
    model = MagicMock(return_value=QaAnswer("回答", grounded=False))
    monkeypatch.setattr(service.qa, "ask", model)
    service.ask("問題", ACTOR)
    model.assert_called_once_with("問題", ACTOR)
    allowed.clear()
    with pytest.raises(RagRuleError):
        service.ask("下一題", ACTOR)
    assert model.call_count == 1


def test_each_question_automatically_uses_latest_department_sources(monkeypatch) -> None:
    allowed = [{"id": KB, "title": "一"}, {"id": OTHER, "title": "二"}]
    monkeypatch.setattr(service, "choices", lambda actor: allowed)
    model = MagicMock(return_value=QaAnswer("回答", grounded=False))
    monkeypatch.setattr(service.qa, "ask", model)
    service.ask("第一題", ACTOR)
    model.assert_called_with("第一題", ACTOR)
    allowed.pop(0)
    service.ask("第二題", ACTOR)
    model.assert_called_with("第二題", ACTOR)


@pytest.mark.parametrize("statement", [repo.SEARCH_CHUNKS, repo.SEARCH_KEYWORDS])
def test_department_sql_keeps_approval_and_source_scope(
    statement,
) -> None:
    sql = str(statement)
    for clause in [
        "da.department_id = CAST(:department_id AS uuid)",
        "da.can_read AND da.use_for_qa",
        "kb.approval_status = 'approved'",
        "kb.is_active",
        "f.is_approved",
        "f.parse_status = 'ready'",
    ]:
        assert clause in sql
    assert ":selected_ids" not in sql


def test_vector_and_keyword_queries_bind_current_department() -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value = []
    repo.search_chunks(db, [0.0], "model", DEPT, 8)
    statement, params = db.execute.call_args.args
    assert statement is repo.SEARCH_CHUNKS
    assert params["department_id"] == str(DEPT) and "selected_ids" not in params
    repo.search_keyword_chunks(db, ["第1條"], 4, department_id=DEPT)
    statement, params = db.execute.call_args.args
    assert statement is repo.SEARCH_KEYWORDS
    assert params["department_id"] == str(DEPT) and "selected_ids" not in params


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(router.router)

    class Module:
        allowed = True

        def identity(self, evidence):
            if evidence is None:
                raise UnauthenticatedError
            return SimpleNamespace(user_id=USER, selected_department_id=DEPT)

        def check(self, identity, code, action, target):
            assert code == "rag.qa" and action == Action.VIEW
            assert target.department_id == DEPT
            if not self.allowed:
                raise DeniedError

        def departments(self, identity):
            return (EffectiveDepartment(DEPT, "finance", "財務部"),)

        def sidebar_menu(self, identity):
            return ()

    module = Module()
    app.dependency_overrides[get_e_module] = lambda: module
    from app.routers.e_admin import EError, handle_e_error

    app.add_exception_handler(EError, handle_e_error)
    monkeypatch.setattr(service, "choices", lambda actor: [{"id": KB, "title": "付款規範"}])
    monkeypatch.setattr(
        service,
        "configuration_status",
        lambda actor: {
            "readable_count": 0,
            "selected_count": 0,
            "available_count": 0,
            "unavailable": [],
        },
    )
    answer = MagicMock(return_value=QaAnswer("回答 <script>", grounded=False))
    monkeypatch.setattr(service, "ask", answer)
    with TestClient(app) as browser:
        browser.cookies.set(SESSION_COOKIE_NAME, "synthetic")
        yield browser, module, answer


def test_page_has_only_chat_and_no_source_names_or_controls(client) -> None:
    browser, _, _ = client
    response = browser.get("/knowledge-qa")
    assert response.status_code == 200
    assert "付款規範" not in response.text and KB not in response.text
    assert 'aria-label="目前部門與可用知識庫"' not in response.text
    assert 'name="knowledge_base_id"' not in response.text
    assert 'class="app-sidebar"' in response.text
    assert "勾選可用知識庫" not in response.text
    assert "qa-layout--department" in response.text
    assert "未指派" not in response.text and "/knowledge/chat" not in response.text
    assert 'name="department_id"' not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_no_sources_disables_question(client, monkeypatch) -> None:
    monkeypatch.setattr(service, "choices", lambda actor: [])
    response = client[0].get("/knowledge-qa")
    assert "尚無可用" in response.text and "disabled" in response.text


@pytest.mark.parametrize("context,status", [(str(DEPT), 200), (OTHER, 409), ("", 409)])
def test_post_uses_session_department_and_rejects_old_page(client, context, status) -> None:
    browser, _, answer = client
    response = browser.post(
        "/knowledge-qa/ask",
        headers={"Origin": "http://testserver"},
        data={
            "question": "<script>",
            "knowledge_base_id": OTHER,
            "department_id": OTHER,
            "context_department_id": context,
        },
    )
    assert response.status_code == status
    if status == 200:
        answer.assert_called_once_with("<script>", ACTOR)
        assert "&lt;script&gt;" in response.text
    else:
        answer.assert_not_called()


def test_post_rejects_cross_origin_before_model(client) -> None:
    browser, _, answer = client
    assert (
        browser.post("/knowledge-qa/ask", headers={"Origin": "https://other.invalid"}).status_code
        == 403
    )
    answer.assert_not_called()


def test_direct_url_requires_qa_permission(client) -> None:
    browser, module, _ = client
    module.allowed = False
    assert browser.get("/knowledge-qa").status_code == 403


def test_direct_url_requires_cookie(client) -> None:
    browser, _, _ = client
    browser.cookies.clear()
    assert browser.get("/knowledge-qa", follow_redirects=False).status_code in {302, 303, 307}
