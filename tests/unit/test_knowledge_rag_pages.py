"""
模組名稱：tests.unit.test_knowledge_rag_pages
功能說明：/knowledge 改接 DB 後的路由測試——畫面資料來自 service（部門讀 DB、無寫死部門）、
          寫入路由依操作呼叫 module.check（CREATE／EDIT／DELETE）、審核需資訊部、操作者取自身分、
          路徑參數須為 UUID、錯誤顯示與 503、上傳結果跳脫 HTML。
相依模組：app.routers.knowledge、app.services.rag_admin_service
注意事項：
    - 依 docs/RAG_DEVELOPMENT.md：以 app.dependency_overrides[require_knowledge_access] 略過查看權限，
      get_e_module 換成只記錄呼叫的替身；權限判斷本身由 test_knowledge_access、test_department_login 測試。
    - rag_admin_service 的函式以替身取代，不連正式 DB、Storage、Google。
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.main import app
from app.routers import knowledge
from app.routers.knowledge import require_knowledge_access
from app.schemas.e_admin import Action, DeniedError, Target
from app.schemas.rag import (
    APPROVAL_PENDING,
    PARSE_FAILED,
    DepartmentAccess,
    DepartmentOption,
    FileDepartmentUsage,
    KnowledgeBase,
    LatestJob,
    RagActor,
    RagFile,
    RagRuleError,
)
from app.services import rag_admin_service
from app.services.e_admin_service import KNOWLEDGE, get_e_module

USER = UUID("00000000-0000-4000-8000-000000000001")
FINANCE = "00000000-0000-4000-8000-00000000000f"
IT = "00000000-0000-4000-8000-00000000000e"
KB = "11111111-1111-4111-8111-111111111111"
FILE = "22222222-2222-4222-8222-222222222222"

DEPARTMENTS = [
    DepartmentOption(FINANCE, "finance_d", "財務部"),
    DepartmentOption(IT, "technology_d", "資訊部"),
]
KB_ROW = KnowledgeBase(
    KB, "營業稅申報", APPROVAL_PENDING, submitted_department_id=FINANCE, knowledge_code="kb-x"
)
ACCESS = (DepartmentAccess(KB, FINANCE, can_manage=True),)
TXT_FILE = RagFile(
    FILE,
    KB,
    "作業要點",
    parse_status=PARSE_FAILED,
    original_filename="要點.txt",
    mime_type="text/plain",
    storage_path="kb-x/RAG_1.txt",
)


class FakeModule:
    """只記錄寫入路由的 module.check；denied 為真時拒絕。"""

    def __init__(self) -> None:
        self.checks: list[tuple[str, Action, Target]] = []
        self.denied = False

    def check(self, identity: Any, code: str, action: Action, target: Target) -> None:
        self.checks.append((code, action, target))
        if self.denied:
            raise DeniedError


@dataclass
class Pages:
    client: TestClient
    module: FakeModule
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    is_it: bool = False

    def called(self, name: str) -> list[tuple[Any, ...]]:
        return [args for called, args in self.calls if called == name]


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> Iterator[Pages]:
    module = FakeModule()
    state = Pages(TestClient(app, follow_redirects=False), module)

    def bypass(request: Request) -> None:
        request.state.knowledge_identity = SimpleNamespace(
            user_id=USER, selected_department_id=UUID(FINANCE)
        )
        request.state.is_it_workspace = state.is_it

    def recorder(name: str, result: Any = None) -> Any:
        def call(*args: Any, **kwargs: Any) -> Any:
            state.calls.append((name, args))
            return result

        return call

    view = rag_admin_service.DocumentsView(
        KB_ROW,
        ACCESS,
        (TXT_FILE,),
        (FileDepartmentUsage(FILE, FINANCE, True),),
        {FILE: LatestJob(FILE, "failed", "rag_pdf_page_limit")},
    )
    reads = {
        "list_departments": lambda: DEPARTMENTS,
        "load_overview": lambda: rag_admin_service.KnowledgeOverview((KB_ROW,), ACCESS),
        "load_knowledge_base": lambda kb_id: (KB_ROW, ACCESS),
        "load_documents": lambda kb_id: view,
        "load_file": lambda kb_id, file_id: (KB_ROW, ACCESS, TXT_FILE),
        "read_file_content": lambda kb_id, file_id: (TXT_FILE, "第一點 要點內容".encode()),
    }
    writes = {
        "approve_knowledge_base": None,
        "reject_knowledge_base": None,
        "resubmit_knowledge_base": None,
        "set_qa_source": None,
        "clear_qa_source": None,
        "set_department_assignment": ACCESS,
        "set_file_usage": view,
        "approve_file": None,
        "suspend_file": None,
        "index_document": None,
        "rename_knowledge_base": None,
        "delete_knowledge_base": None,
        "rename_file": None,
        "delete_file": None,
        "replace_file_content": None,
        "create_knowledge_base": KB_ROW,
        "upload_documents": [rag_admin_service.UploadOutcome("<b>x.txt", True, "已上傳")],
    }
    for name, func in reads.items():
        monkeypatch.setattr(rag_admin_service, name, func)
    for name, result in writes.items():
        monkeypatch.setattr(rag_admin_service, name, recorder(name, result))
    app.dependency_overrides[require_knowledge_access] = bypass
    app.dependency_overrides[get_e_module] = lambda: module
    yield state
    app.dependency_overrides.clear()


# ---- 畫面資料 ----


def test_list_reads_departments_from_db_and_limits_qa_to_own_department(pages: Pages) -> None:
    response = pages.client.get("/knowledge")

    assert response.status_code == 200
    assert not hasattr(knowledge, "DEPARTMENTS")
    assert "營業稅申報" in response.text and "待審核" in response.text
    assert "財務部" in response.text and "資訊部" in response.text
    assert "尚無已核准並指派給此部門的知識庫" in response.text  # 待審核的知識庫不可選用
    assert "dept-badge--owner" in response.text and "財務部 · 上傳" in response.text
    assert pages.module.checks == []  # 查看不另呼叫寫入檢查


def test_qa_choice_of_other_department_is_read_only(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = KnowledgeBase(KB, "營業稅申報", "approved", submitted_department_id=FINANCE)
    second = KnowledgeBase("33333333-3333-4333-8333-333333333333", "會計法規", "approved")
    rows = (
        *ACCESS,
        DepartmentAccess(KB, IT),
        DepartmentAccess(second.knowledge_base_id, IT),
    )
    monkeypatch.setattr(
        rag_admin_service,
        "load_overview",
        lambda: rag_admin_service.KnowledgeOverview((approved, second), rows),
    )

    response = pages.client.get("/knowledge")

    assert "由該部門設定" in response.text  # 資訊部那一列不能由財務部設定
    assert "可選 2 個，同時只能用 1 個" in response.text


def test_documents_page_shows_index_failure_and_hides_it_actions(pages: Pages) -> None:
    response = pages.client.get(f"/knowledge/{KB}/documents")

    assert response.status_code == 200
    assert "PDF 超過 15 頁" in response.text
    assert f"/documents/{FILE}/index" not in response.text  # 上傳部門不能建立索引
    assert f"/documents/{FILE}/status" not in response.text  # 非資訊部看不到核准


def test_it_sees_index_button(pages: Pages) -> None:
    pages.is_it = True

    response = pages.client.get(f"/knowledge/{KB}/documents")

    assert f"/knowledge/{KB}/documents/{FILE}/index" in response.text


def test_content_page_reads_text_and_download_returns_original(pages: Pages) -> None:
    content = pages.client.get(f"/knowledge/{KB}/documents/{FILE}/content")
    download = pages.client.get(f"/knowledge/{KB}/documents/{FILE}/download")

    assert "第一點 要點內容" in content.text
    assert download.content == "第一點 要點內容".encode()
    assert download.headers["content-type"].startswith("text/plain")
    assert "attachment" in download.headers["content-disposition"]


# ---- 寫入權限 ----


@pytest.mark.parametrize(
    "path,data,action,service",
    [
        (f"/knowledge/{KB}/rename", {"title": "新名稱"}, Action.EDIT, "rename_knowledge_base"),
        (f"/knowledge/{KB}/delete", {}, Action.DELETE, "delete_knowledge_base"),
        (
            f"/knowledge/{KB}/toggle-dept",
            {"department_id": IT, "enable": "true"},
            Action.EDIT,
            "set_department_assignment",
        ),
        (
            f"/knowledge/{KB}/documents/{FILE}/toggle-dept",
            {"department_id": FINANCE, "enable": "false"},
            Action.EDIT,
            "set_file_usage",
        ),
        (f"/knowledge/{KB}/documents/{FILE}/rename", {"title": "x"}, Action.EDIT, "rename_file"),
        (f"/knowledge/{KB}/documents/{FILE}/delete", {}, Action.DELETE, "delete_file"),
        (
            f"/knowledge/{KB}/documents/{FILE}/content",
            {"text": "新內容"},
            Action.EDIT,
            "replace_file_content",
        ),
        ("/knowledge/qa-source", {"department_id": FINANCE}, Action.EDIT, "clear_qa_source"),
        (f"/knowledge/{KB}/review/resubmit", {}, Action.EDIT, "resubmit_knowledge_base"),
    ],
)
def test_write_routes_check_matching_action(
    pages: Pages, path: str, data: dict[str, str], action: Action, service: str
) -> None:
    response = pages.client.post(path, data=data)

    assert response.status_code in {200, 303}
    assert pages.module.checks == [(KNOWLEDGE, action, Target(UUID(FINANCE)))]
    assert len(pages.called(service)) == 1


def test_upload_requires_create_and_uses_identity_department(pages: Pages) -> None:
    response = pages.client.post(
        "/knowledge/documents",
        data={"knowledge_base_id": KB, "department_id": IT},
        files=[("files", ("<b>x.txt", b"x", "text/plain"))],
    )

    assert response.status_code == 200
    assert pages.module.checks == [(KNOWLEDGE, Action.CREATE, Target(UUID(FINANCE)))]
    (args,) = pages.called("upload_documents")
    assert args[2] == RagActor(str(USER), FINANCE, False)  # 表單的 department_id 不被採用
    assert "&lt;b&gt;x.txt" in response.text and "<b>x.txt" not in response.text


def test_upload_with_new_title_creates_knowledge_base_first(pages: Pages) -> None:
    pages.client.post(
        "/knowledge/documents",
        data={"knowledge_base_title": "採購法規"},
        files=[("files", ("a.txt", b"x", "text/plain"))],
    )

    assert pages.called("create_knowledge_base")[0][0] == "採購法規"
    assert pages.called("upload_documents")[0][0] == KB


def test_denied_write_does_not_call_service(pages: Pages) -> None:
    pages.module.denied = True

    response = pages.client.post(f"/knowledge/{KB}/delete")

    assert response.status_code == 403
    assert pages.calls == []


@pytest.mark.parametrize(
    "path,data",
    [
        (f"/knowledge/{KB}/review/approve", {}),
        (f"/knowledge/{KB}/review/reject", {"comment": "內容過舊"}),
        (f"/knowledge/{KB}/documents/{FILE}/status", {"status": "active"}),
        (f"/knowledge/{KB}/documents/{FILE}/index", {}),
    ],
)
def test_review_requires_it_workspace(pages: Pages, path: str, data: dict[str, str]) -> None:
    denied = pages.client.post(path, data=data)
    assert denied.status_code == 403 and pages.calls == []

    pages.is_it = True
    allowed = pages.client.post(path, data=data)

    assert allowed.status_code == 303
    assert len(pages.calls) == 1
    assert all(check[1] == Action.EDIT for check in pages.module.checks)


def test_approve_passes_reviewer_uuid_from_identity(pages: Pages) -> None:
    pages.is_it = True

    pages.client.post(f"/knowledge/{KB}/review/approve")

    assert pages.called("approve_knowledge_base") == [(KB, USER, True)]


# ---- 錯誤處理 ----


def test_non_uuid_path_is_not_found(pages: Pages) -> None:
    assert pages.client.get("/knowledge/shared-tax/documents").status_code == 404
    assert pages.client.post("/knowledge/shared-tax/rename", data={"title": "x"}).status_code == 404
    assert pages.calls == []


def test_rule_error_is_shown_on_page(pages: Pages, monkeypatch: pytest.MonkeyPatch) -> None:
    def duplicate(*args: Any) -> None:
        raise RagRuleError("DUPLICATE_NAME", "已有相同名稱的知識庫")

    monkeypatch.setattr(rag_admin_service, "rename_knowledge_base", duplicate)

    response = pages.client.post(f"/knowledge/{KB}/rename", data={"title": "重複"})

    assert response.status_code == 400
    assert "已有相同名稱的知識庫" in response.text


def test_database_unavailable_returns_503(pages: Pages, monkeypatch: pytest.MonkeyPatch) -> None:
    def down() -> None:
        raise rag_admin_service.RagUnavailableError("資料庫未設定或無法連線")

    monkeypatch.setattr(rag_admin_service, "load_overview", down)

    assert pages.client.get("/knowledge").status_code == 503


# ---- 問答 ----


def test_chat_page_lists_knowledge_bases_for_it(pages: Pages) -> None:
    pages.is_it = True

    response = pages.client.get("/knowledge/chat")

    assert response.status_code == 200
    assert "知識庫小助理" in response.text and "營業稅申報（待審核）" in response.text


def test_chat_ask_renders_escaped_question_and_sources(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import rag_qa_service

    answer = rag_qa_service.QaAnswer(
        "要取得影本 [1]", True, (rag_qa_service.QaSource(1, "統一發票使用辦法", "營業稅", 2),), 30
    )
    seen: list[tuple[str, RagActor, str | None]] = []
    monkeypatch.setattr(
        rag_qa_service, "ask", lambda q, actor, kb: seen.append((q, actor, kb)) or answer
    )

    response = pages.client.post(
        "/knowledge/chat/ask", data={"question": "<b>遺失</b>", "knowledge_base_id": KB}
    )

    assert response.status_code == 200
    assert "&lt;b&gt;遺失&lt;/b&gt;" in response.text
    assert "《統一發票使用辦法》第 3 段" in response.text
    assert seen[0][2] == KB and seen[0][1].department_id == FINANCE
    assert pages.module.checks == []  # 問答只需查看權限


def test_upload_accepts_one_file_at_a_time(pages: Pages) -> None:
    response = pages.client.post(
        "/knowledge/documents",
        data={"knowledge_base_id": KB},
        files=[
            ("files", ("a.txt", b"x", "text/plain")),
            ("files", ("b.txt", b"y", "text/plain")),
        ],
    )

    assert response.status_code == 400 and "一次上傳一份文件" in response.text
    assert pages.called("upload_documents") == []


def test_it_indexes_next_pending_file_from_list(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    overview = rag_admin_service.KnowledgeOverview((KB_ROW,), ACCESS, {KB: [FILE, "f-2"]})
    monkeypatch.setattr(rag_admin_service, "load_overview", lambda: overview)

    assert pages.client.post(f"/knowledge/{KB}/index-next").status_code == 403
    pages.is_it = True
    assert "建立索引（剩 2 份）" in pages.client.get("/knowledge").text

    response = pages.client.post(f"/knowledge/{KB}/index-next")

    assert response.status_code == 303
    (args,) = pages.called("index_document")
    assert args[:2] == (KB, FILE)


def test_it_requeues_indexed_knowledge_base(pages: Pages, monkeypatch: pytest.MonkeyPatch) -> None:
    overview = rag_admin_service.KnowledgeOverview((KB_ROW,), ACCESS, {}, {KB: 1})
    monkeypatch.setattr(rag_admin_service, "load_overview", lambda: overview)
    queued: list[str] = []
    monkeypatch.setattr(rag_admin_service, "queue_reindex", lambda kb, actor: queued.append(kb))

    assert pages.client.post(f"/knowledge/{KB}/reindex").status_code == 403
    pages.is_it = True
    assert f"/knowledge/{KB}/reindex" in pages.client.get("/knowledge").text
    assert pages.client.post(f"/knowledge/{KB}/reindex").status_code == 303
    assert queued == [KB]
