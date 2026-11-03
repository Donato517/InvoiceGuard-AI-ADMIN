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

import inspect
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path
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
    KB,
    "營業稅申報",
    APPROVAL_PENDING,
    submitted_department_id=FINANCE,
    knowledge_code="kb-x",
    file_count=1,
)
ACCESS = (DepartmentAccess(KB, FINANCE, can_manage=True),)
TXT_FILE = RagFile(
    FILE,
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
        self.delete_allowed = True

    def check(self, identity: Any, code: str, action: Action, target: Target) -> None:
        self.checks.append((code, action, target))
        if self.denied or (action == Action.DELETE and not self.delete_allowed):
            raise DeniedError


@dataclass
class Pages:
    client: TestClient
    module: FakeModule
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    is_it: bool = False
    can_delete: bool = True

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
        request.state.knowledge_can_delete = state.can_delete

    def recorder(name: str, result: Any = None) -> Any:
        signature = inspect.signature(getattr(rag_admin_service, name))

        def call(*args: Any, **kwargs: Any) -> Any:
            signature.bind(*args, **kwargs)
            state.calls.append((name, args))
            return result

        return call

    view = rag_admin_service.DocumentsView(
        KB_ROW,
        ACCESS,
        (TXT_FILE,),
        {FILE: LatestJob(FILE, "failed", "rag_pdf_page_limit")},
    )
    reads = {
        "list_departments": lambda: DEPARTMENTS,
        "load_overview": lambda: rag_admin_service.KnowledgeOverview((KB_ROW,), ACCESS),
        "load_knowledge_base": lambda kb_id: (KB_ROW, ACCESS),
        "load_documents": lambda kb_id: view,
        "load_file_library": lambda: rag_admin_service.FileLibrary(
            (TXT_FILE,), {FILE: (KB_ROW,)}, view.latest_jobs
        ),
        "load_file": lambda file_id: TXT_FILE,
        "read_file_content": lambda file_id: (TXT_FILE, "第一點 要點內容".encode()),
    }
    writes = {
        "approve_knowledge_base": None,
        "reject_knowledge_base": None,
        "resubmit_knowledge_base": None,
        "set_qa_sources": None,
        "set_department_assignment": ACCESS,
        "approve_file": None,
        "suspend_file": None,
        "index_document": None,
        "rename_knowledge_base": None,
        "delete_knowledge_base": None,
        "rename_file": None,
        "delete_file": None,
        "replace_file_content": None,
        "create_knowledge_base": KB_ROW,
        "copy_knowledge_base": KB_ROW,
        "add_files": None,
        "remove_file": None,
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
    assert "營業稅申報" in response.text and "待審查" in response.text
    assert "財務部" in response.text and "資訊部" in response.text
    assert "尚無已核准、非空並開放給此部門的知識庫" in response.text  # 待審查的知識庫不可選用
    assert "dept-badge--owner" in response.text and "財務部 · 上傳" in response.text
    assert pages.module.checks == []  # 查看不另呼叫寫入檢查


def test_qa_choice_of_other_department_is_read_only(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = KnowledgeBase(
        KB, "營業稅申報", "approved", submitted_department_id=FINANCE, file_count=1
    )
    second = KnowledgeBase(
        "33333333-3333-4333-8333-333333333333", "會計法規", "approved", file_count=1
    )
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
    assert "已選 0／2 個" in response.text


def test_documents_page_shows_index_failure_and_hides_it_actions(pages: Pages) -> None:
    response = pages.client.get("/knowledge/files")

    assert response.status_code == 200
    assert "PDF 超過 15 頁" in response.text
    assert f"/knowledge/files/{FILE}/index" not in response.text  # 上傳部門不能建立索引
    assert f"/knowledge/files/{FILE}/status" not in response.text  # 非資訊部看不到核准


def test_it_sees_index_button(pages: Pages) -> None:
    pages.is_it = True

    response = pages.client.get("/knowledge/files")

    assert f"/knowledge/files/{FILE}/index" in response.text


def test_content_page_reads_text_and_download_returns_original(pages: Pages) -> None:
    content = pages.client.get(f"/knowledge/files/{FILE}/content")
    download = pages.client.get(f"/knowledge/files/{FILE}/download")

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
        (f"/knowledge/files/{FILE}/rename", {"title": "x"}, Action.EDIT, "rename_file"),
        (f"/knowledge/files/{FILE}/delete", {}, Action.DELETE, "delete_file"),
        (
            f"/knowledge/files/{FILE}/content",
            {"text": "新內容"},
            Action.EDIT,
            "replace_file_content",
        ),
        ("/knowledge/qa-source", {"department_id": FINANCE}, Action.EDIT, "set_qa_sources"),
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
        "/knowledge/files/upload",
        data={
            "knowledge_base_id": KB,
            "department_id": IT,
            "source_type": "company_policy",
            "effective_date_status": "unknown",
        },
        files=[("files", ("<b>x.txt", b"x", "text/plain"))],
    )

    assert response.status_code == 200
    assert pages.module.checks == [(KNOWLEDGE, Action.CREATE, Target(UUID(FINANCE)))]
    (args,) = pages.called("upload_documents")
    assert args[2] == RagActor(str(USER), FINANCE, False)  # 表單的 department_id 不被採用
    assert "&lt;b&gt;x.txt" in response.text and "<b>x.txt" not in response.text


def test_create_knowledge_base_then_select_files(pages: Pages) -> None:
    response = pages.client.post("/knowledge/create", data={"title": "採購法規"})
    assert response.status_code == 303
    assert pages.called("create_knowledge_base")[0][0] == "採購法規"
    assert response.headers["location"] == f"/knowledge/{KB}/documents"
    assert pages.called("upload_documents") == []


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
        (f"/knowledge/files/{FILE}/status", {"status": "active"}),
        (f"/knowledge/files/{FILE}/index", {}),
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


@pytest.mark.parametrize(
    "path,data,service,heading",
    [
        (f"/knowledge/files/{FILE}/delete", {}, "delete_file", "檔案管理"),
        (f"/knowledge/files/{FILE}/rename", {"title": "x"}, "rename_file", "檔案管理"),
        (
            f"/knowledge/files/{FILE}/content",
            {"text": "新內容"},
            "replace_file_content",
            "文件內容與編輯",
        ),
        (f"/knowledge/files/{FILE}/status", {"status": "active"}, "approve_file", "檔案管理"),
        (f"/knowledge/files/{FILE}/index", {}, "index_document", "檔案管理"),
    ],
)
def test_blocked_file_operation_returns_full_page_with_reason(
    pages: Pages,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    data: dict[str, str],
    service: str,
    heading: str,
) -> None:
    def blocked(*args: Any) -> None:
        raise RagRuleError("FILE_REFERENCED", "檔案仍被知識庫引用：<b>營業稅申報</b>")

    pages.is_it = True
    monkeypatch.setattr(rag_admin_service, service, blocked)

    response = pages.client.post(path, data=data)

    assert response.status_code == 400
    assert "<html" in response.text and f"<h2>{heading}</h2>" in response.text
    assert "檔案仍被知識庫引用：&lt;b&gt;營業稅申報&lt;/b&gt;" in response.text


def test_content_save_without_changes_returns_full_page(pages: Pages) -> None:
    response = pages.client.post(
        f"/knowledge/files/{FILE}/content",
        files={"file": ("", b"", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert "<html" in response.text and "請修改文字或選擇替換檔案" in response.text


@pytest.mark.parametrize("path", ["/knowledge/files", f"/knowledge/{KB}/documents"])
def test_long_file_names_wrap_so_action_column_stays_on_screen(pages: Pages, path: str) -> None:
    # 共用 .table 不換行，長檔名會把「操作」欄擠出畫面，看不到核准與移除按鈕
    assert 'class="cell-wrap"' in pages.client.get(path).text


def test_failed_upload_does_not_switch_dialog_to_done(pages: Pages) -> None:
    # 全域設定會替換 400，event.detail.successful 仍為真，要看實際狀態碼才不會標成完成
    page = pages.client.get("/knowledge/files").text
    assert "toggle('is-done', event.detail.xhr.status === 200)" in page


def test_closing_dialog_discards_typed_values() -> None:
    script = Path("app/static/js/main.js").read_text(encoding="utf-8")
    handler = script.split("closeTrigger.closest('dialog')", 1)[1]
    assert handler.index("reset()") < handler.index("dialog.close()")


def test_cell_wrap_also_breaks_long_latin_names() -> None:
    # break-word 不會縮小表格欄的最小寬度，連續英數檔名仍會把操作欄擠出畫面
    stylesheet = Path("app/static/css/pages/knowledge.css").read_text(encoding="utf-8")
    assert "overflow-wrap: anywhere" in stylesheet.split(".cell-wrap", 1)[1]


def test_in_use_count_requires_department_assignment(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    second = "33333333-3333-4333-8333-333333333333"
    kbs = (KB_ROW, KnowledgeBase(second, "只開放未指派", "approved", file_count=1))
    rows = (DepartmentAccess(KB, FINANCE, use_for_qa=True), DepartmentAccess(second, FINANCE))
    monkeypatch.setattr(
        rag_admin_service, "load_overview", lambda: rag_admin_service.KnowledgeOverview(kbs, rows)
    )

    response = pages.client.get("/knowledge")

    assert re.findall(r'stat-card__value">(\d+)<', response.text) == ["2", "1", "1"]


def test_knowledge_base_files_page_searches_filters_and_counts(pages: Pages) -> None:
    listed = pages.client.get(f"/knowledge/{KB}/documents")
    searched = pages.client.get(f"/knowledge/{KB}/documents", params={"q": "不存在"})
    filtered = pages.client.get(f"/knowledge/{KB}/documents", params={"approval": "approved"})

    assert "要點.txt" in listed.text
    assert re.findall(r'stat-card__value">(\d+)<', listed.text) == ["1", "0", "1"]
    assert "沒有符合條件的檔案" in searched.text and "要點.txt" not in searched.text
    assert "要點.txt" not in filtered.text and "selected>已核准檔案" in filtered.text


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
    assert "<h2>知識庫問答</h2>" in response.text and "清除對話" in response.text
    assert "A/B" not in response.text
    elements = HtmlElements(response.text)
    assert len(elements.elements("textarea", name="question")) == 1
    assert len(elements.elements("select", name="department_id")) == 1
    assert "依資訊部已指派的全部知識庫" not in response.text


def test_chat_page_preselects_login_department_and_its_assigned_knowledge(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    """打開問答頁時預選登入者目前的部門（測試替身為財務部），並勾選該部門指派的知識庫。"""
    pages.is_it = True
    kbs = (
        replace(KB_ROW, approval_status="approved"),
        KnowledgeBase(SECOND_KB, "會計法規", "approved", file_count=1),
    )
    rows = (
        DepartmentAccess(KB, FINANCE, use_for_qa=True),
        DepartmentAccess(SECOND_KB, IT, use_for_qa=True),
    )
    monkeypatch.setattr(
        rag_admin_service, "load_overview", lambda: rag_admin_service.KnowledgeOverview(kbs, rows)
    )

    html = pages.client.get("/knowledge/chat").text

    assert re.search(rf'<option value="{FINANCE}"\s+selected>財務部</option>', html)
    choices = html[html.index('id="chat-choices"') :]
    checked = re.findall(r'value="([0-9a-f-]{36})"\s+checked', choices)
    assert checked == [KB], choices[:1500]


def test_switching_department_keeps_typed_question_and_shows_no_answer_overlay(
    pages: Pages,
) -> None:
    pages.is_it = True

    response = pages.client.get("/knowledge/chat")

    # 切換部門的請求也會冒泡到表單，不可觸發問答的遮罩或清空輸入框
    [select] = HtmlElements(response.text).elements("select", name="department_id")
    assert select["data-busy"] == "" and select["hx-include"] == "#chat-choices"
    assert "event.detail.elt === this" in response.text
    # 400 也會顯示訊息泡泡（successful 為真）；只有成功回答才清空，沒勾知識庫時問題要留著
    assert "event.detail.xhr.status === 200" in response.text


@pytest.mark.parametrize(
    "text,status",
    [("SERVICE_UNAVAILABLE", 400), ("UNVERIFIED_ANSWER", 400), ("NO_ANSWER", 200)],
)
def test_chat_ask_status_tells_page_whether_to_keep_the_question(
    pages: Pages, monkeypatch: pytest.MonkeyPatch, text: str, status: int
) -> None:
    from app.services import rag_qa_service as qa

    pages.is_it = True
    message = getattr(qa, text)
    monkeypatch.setattr(
        qa, "ask_test", lambda *args: qa.QaAnswer(message, False, note="向量服務錯誤：逾時")
    )

    response = pages.client.post(
        "/knowledge/chat/ask", data={"question": "遺失怎麼辦", "knowledge_base_id": KB}
    )

    assert response.status_code == status
    assert "遺失怎麼辦" in response.text and message.splitlines()[0] in response.text
    assert "向量服務錯誤：逾時" in response.text


def test_chat_ask_renders_escaped_question_and_sources(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import rag_qa_service

    pages.is_it = True
    answer = rag_qa_service.QaAnswer(
        "<b>要取得影本</b> [1]",
        True,
        (rag_qa_service.QaSource(1, "統一發票使用辦法", "營業稅", 2),),
        30,
        note="<測試>",
    )
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(rag_qa_service, "ask_test", lambda *args: seen.append(args) or answer)

    response = pages.client.post(
        "/knowledge/chat/ask", data={"question": "<b>遺失</b>", "knowledge_base_id": KB}
    )

    assert response.status_code == 200
    assert "&lt;b&gt;遺失&lt;/b&gt;" in response.text
    assert "&lt;b&gt;要取得影本&lt;/b&gt;" in response.text
    assert "知識庫「營業稅」《統一發票使用辦法》第 3 段" in response.text
    assert seen[0][2] == [KB] and seen[0][1].department_id == FINANCE
    assert "&lt;測試&gt;" in response.text
    assert pages.module.checks == []  # 問答只需查看權限


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/knowledge/chat"),
        ("GET", "/knowledge/chat/choices"),
        ("POST", "/knowledge/chat/ask"),
    ],
)
def test_chat_is_it_only(pages: Pages, method: str, path: str) -> None:
    assert pages.client.request(method, path).status_code == 403


@pytest.mark.parametrize("department", ["", FINANCE])
@pytest.mark.parametrize("ids", [[KB], [KB, "33333333-3333-4333-8333-333333333333"]])
def test_four_selections_forward_authenticated_actor_and_all_ids(
    pages: Pages,
    monkeypatch: pytest.MonkeyPatch,
    department: str,
    ids: list[str],
) -> None:
    from app.services import rag_qa_service as qa

    pages.is_it = True
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        qa, "ask_test", lambda *args: seen.append(args) or qa.QaAnswer("回答", False)
    )
    response = pages.client.post(
        "/knowledge/chat/ask",
        data={
            "question": "同一問題",
            "knowledge_base_id": ids,
            "department_id": department,
            "user_id": "spoofed",
            "department_name": "假名稱",
            "knowledge_name": "假名稱",
        },
    )
    assert response.status_code == 200
    assert seen == [("同一問題", RagActor(str(USER), FINANCE, True), ids, department or None)]
    assert "假名稱" not in response.text


@pytest.mark.parametrize(
    "changes",
    [
        {"approval_status": "pending"},
        {"approval_status": "rejected"},
        {"is_active": False},
        {"file_count": 0},
    ],
)
def test_direct_test_post_rechecks_availability_before_models(
    pages: Pages,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, Any],
) -> None:
    from app.services import rag_qa_service as qa

    pages.is_it = True
    unavailable = replace(replace(KB_ROW, approval_status="approved"), **changes)
    monkeypatch.setattr(qa.rag_repository, "get_knowledge_base", lambda *args: unavailable)

    class Session:
        def close(self) -> None:
            pass

    # ask_test 的正式 factory 不連線；以既有入口的 keyword default 注入離線 Session。
    monkeypatch.setitem(qa.ask_test.__kwdefaults__, "open_session", Session)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("不可用知識庫不應開始檢索或模型")

    monkeypatch.setattr(qa, "_answer", forbidden)
    response = pages.client.post(
        "/knowledge/chat/ask", data={"question": "問題", "knowledge_base_id": KB}
    )
    assert response.status_code == 400 and "只能測試已核准" in response.text


def test_test_form_errors_are_visible_and_all_ids_are_validated(
    pages: Pages,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import rag_qa_service as qa

    pages.is_it = True
    for data in ({"question": "問題"}, {"question": ""}, {"question": "字" * 501}):
        assert pages.client.post("/knowledge/chat/ask", data=data).status_code == 400
    called: list[tuple[Any, ...]] = []
    monkeypatch.setattr(qa, "ask_test", lambda *args: called.append(args))
    for data in (
        {"knowledge_base_id": [KB, "bad"]},
        {"knowledge_base_id": [KB], "department_id": "bad"},
    ):
        assert (
            pages.client.post("/knowledge/chat/ask", data={"question": "問題", **data}).status_code
            == 404
        )
    assert not called
    assert (
        pages.client.get("/knowledge/chat/choices", params={"department_id": FILE}).status_code
        == 400
    )


def test_general_department_has_no_test_page_link(pages: Pages) -> None:
    assert 'href="/knowledge/chat"' not in pages.client.get("/knowledge").text
    pages.is_it = True
    assert 'href="/knowledge/chat"' in pages.client.get("/knowledge").text


def test_upload_accepts_one_file_at_a_time(pages: Pages) -> None:
    response = pages.client.post(
        "/knowledge/files/upload",
        data={
            "knowledge_base_id": KB,
            "source_type": "company_policy",
            "effective_date_status": "unknown",
        },
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
    assert args == (FILE, RagActor(str(USER), FINANCE, True))


def test_it_requeues_indexed_knowledge_base(pages: Pages, monkeypatch: pytest.MonkeyPatch) -> None:
    overview = rag_admin_service.KnowledgeOverview((KB_ROW,), ACCESS, {})
    monkeypatch.setattr(rag_admin_service, "load_overview", lambda: overview)
    queued: list[str] = []
    monkeypatch.setattr(rag_admin_service, "queue_reindex", lambda kb, actor: queued.append(kb))

    assert pages.client.post(f"/knowledge/{KB}/reindex").status_code == 403
    pages.is_it = True
    assert f"/knowledge/{KB}/reindex" in pages.client.get("/knowledge").text
    assert pages.client.post(f"/knowledge/{KB}/reindex").status_code == 303
    assert queued == [KB]


SECOND_KB = "33333333-3333-4333-8333-333333333333"


@pytest.mark.parametrize("selection", [[KB, SECOND_KB], [KB, KB], []])
def test_qa_source_accepts_multiple_form_values_or_empty(
    pages: Pages, selection: list[str]
) -> None:
    response = pages.client.post(
        "/knowledge/qa-source", data={"department_id": FINANCE, "knowledge_base_id": selection}
    )
    assert response.status_code == 303
    assert pages.called("set_qa_sources") == [(selection, FINANCE, RagActor(str(USER), FINANCE))]
    assert pages.module.checks == [(KNOWLEDGE, Action.EDIT, Target(UUID(FINANCE)))]


@pytest.mark.parametrize("selection", [[KB, "invalid"], ["", KB]])
def test_every_qa_source_id_requires_uuid(pages: Pages, selection: list[str]) -> None:
    response = pages.client.post(
        "/knowledge/qa-source", data={"department_id": FINANCE, "knowledge_base_id": selection}
    )
    assert response.status_code == 404
    assert pages.called("set_qa_sources") == []


def test_old_document_usage_route_is_404(pages: Pages) -> None:
    response = pages.client.post(
        f"/knowledge/{KB}/documents/{FILE}/toggle-dept",
        data={"department_id": FINANCE, "enable": "true"},
    )
    assert response.status_code == 404
    assert pages.calls == [] and pages.module.checks == []
    documents = pages.client.get(f"/knowledge/{KB}/documents").text
    assert "在使用部門" not in documents and f"/documents/{FILE}/toggle-dept" not in documents
    assert f"/knowledge/{KB}/toggle-dept" in documents  # 知識庫層保留


class HtmlElements(HTMLParser):
    """以標準庫解析實際 HTML，檢查表單與勾選框屬性。"""

    def __init__(self, markup: str) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.feed(markup)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))

    def elements(self, tag: str, **attrs: str) -> list[dict[str, str | None]]:
        return [
            values
            for name, values in self.tags
            if name == tag and all(values.get(k) == v for k, v in attrs.items())
        ]


def test_checkbox_list_shows_selected_count_and_disables_other_department(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    kbs = (
        replace(KB_ROW, approval_status="approved"),
        KnowledgeBase(SECOND_KB, "會計法規", "approved", file_count=1),
    )
    rows = tuple(
        DepartmentAccess(kb.knowledge_base_id, dept, use_for_qa=True)
        for kb in kbs
        for dept in (FINANCE, IT)
    )
    monkeypatch.setattr(
        rag_admin_service, "load_overview", lambda: rag_admin_service.KnowledgeOverview(kbs, rows)
    )
    response = pages.client.get("/knowledge")
    assert "部門指派（可多選）" in response.text
    forms = re.findall(
        r'<form[^>]*action="/knowledge/qa-source"[^>]*>(.*?)</form>', response.text, re.S
    )
    assert len(forms) == 2
    for markup in forms:
        form = HtmlElements(markup)
        dept = form.elements("input", name="department_id")[0]["value"]
        assert form.elements("fieldset") and form.elements("legend")
        boxes = form.elements("input", type="checkbox", name="knowledge_base_id")
        assert {b["value"] for b in boxes} == {KB, SECOND_KB}
        assert all("checked" in b for b in boxes)
        assert all(("disabled" in b) == (dept == IT) for b in boxes)
        assert "已選 2／2 個" in markup
        assert ("由該部門設定" in markup) == (dept == IT)
    pages.is_it = True
    boxes = HtmlElements(pages.client.get("/knowledge").text).elements(
        "input", name="knowledge_base_id"
    )
    assert boxes and all("disabled" not in b for b in boxes)


def test_selected_count_ignores_knowledge_base_no_longer_selectable(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    kbs = (
        replace(KB_ROW, approval_status="approved"),
        KnowledgeBase(SECOND_KB, "已停用法規", "approved", is_active=False, file_count=1),
    )
    rows = (
        DepartmentAccess(KB, FINANCE, use_for_qa=True),
        DepartmentAccess(SECOND_KB, FINANCE, use_for_qa=True),
    )
    monkeypatch.setattr(
        rag_admin_service, "load_overview", lambda: rag_admin_service.KnowledgeOverview(kbs, rows)
    )

    assert "已選 1／1 個" in pages.client.get("/knowledge").text


@pytest.mark.parametrize("department", ["", FINANCE, IT])
def test_chat_choices_follow_selected_department_and_keep_ticks(
    pages: Pages, monkeypatch: pytest.MonkeyPatch, department: str
) -> None:
    pages.is_it = True
    third = "44444444-4444-4444-8444-444444444444"
    kbs = (
        replace(KB_ROW, knowledge_name="B 營業稅", approval_status="approved"),
        KnowledgeBase(SECOND_KB, "A 會計法規", "approved", file_count=1),
        KnowledgeBase("pending", "未核准", "pending", file_count=1),
        KnowledgeBase("inactive", "已停用", "approved", is_active=False, file_count=1),
        KnowledgeBase("rejected", "已退回", "rejected", file_count=1),
        KnowledgeBase("empty", "空知識庫", "approved", file_count=0),
        KnowledgeBase(third, "C 未指派資料", "approved", file_count=1),
    )
    rows = (
        DepartmentAccess(KB, FINANCE, use_for_qa=True),
        DepartmentAccess(SECOND_KB, IT, use_for_qa=True),
    )
    monkeypatch.setattr(
        rag_admin_service, "load_overview", lambda: rag_admin_service.KnowledgeOverview(kbs, rows)
    )
    response = pages.client.get(
        "/knowledge/chat/choices",
        params={"department_id": department, "knowledge_base_id": [KB, third]},
    )
    assert response.status_code == 200
    fields = re.findall(r"<fieldset.*?</fieldset>", response.text, re.S)
    if department:
        assert len(fields) == 2 and "已指派" in fields[0] and "未指派" in fields[1]
        assigned = {b["value"] for b in HtmlElements(fields[0]).elements("input")}
        assert assigned == ({KB} if department == FINANCE else {SECOND_KB})
    else:
        # 不選部門就沒有「指派給誰」，不可把其他部門的指派顯示成已指派
        assert re.findall(r"<legend>(.*?)</legend>", response.text) == ["全部知識庫"]
    boxes = HtmlElements(response.text).elements("input", name="knowledge_base_id")
    assert {b["value"] for b in boxes} == {KB, SECOND_KB, third}
    assert {b["value"] for b in boxes if "checked" in b} == {KB, third}
    assert all("disabled" not in b for b in boxes)


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("GET", "edit"),
        ("GET", "content"),
        ("GET", "download"),
        ("POST", "rename"),
        ("POST", "delete"),
        ("POST", "content"),
        ("POST", "status"),
        ("POST", "index"),
        ("POST", "toggle-dept"),
    ],
)
def test_all_old_file_operation_routes_are_404(pages: Pages, method: str, suffix: str) -> None:
    response = pages.client.request(method, f"/knowledge/{KB}/documents/{FILE}/{suffix}")
    assert response.status_code == 404 and pages.calls == []
    assert pages.module.checks == []


def test_old_upload_route_is_404(pages: Pages) -> None:
    assert pages.client.post("/knowledge/documents").status_code == 404
    assert pages.calls == []


def test_file_library_exposes_states_metadata_and_references(pages: Pages) -> None:
    response = pages.client.get("/knowledge/files")
    assert response.status_code == 200
    for label in [
        "檔案管理",
        "作業要點",
        "待審查",
        "索引失敗",
        "未分類",
        "未知",
        "引用的知識庫",
        "營業稅申報",
    ]:
        assert label in response.text
    assert "PDF 超過 15 頁" in response.text
    assert "/knowledge/files/upload" in response.text
    form = HtmlElements(response.text)
    sources = form.elements("select", name="source_type")
    assert len(sources) == 1 and "required" in sources[0]
    assert 'value="unclassified"' not in response.text
    assert form.elements("select", name="effective_date_status")
    assert 'name="source_note"' in response.text and 'name="effective_date"' in response.text
    assert (
        pages.client.get("/knowledge/files", params={"q": "不存在"}).text.find("沒有符合條件的檔案")
        >= 0
    )


def test_last_file_removal_has_reviewable_impact_and_count(pages: Pages) -> None:
    response = pages.client.get(f"/knowledge/{KB}/documents")
    assert response.status_code == 200
    assert "將收回所有部門指派" in response.text
    assert 'name="expected_count" value="1"' in response.text
    assert "不刪除原檔與索引" in response.text
    assert f'data-open-dialog="remove-file-{FILE}"' in response.text
    assert pages.calls == []
    saved = pages.client.post(f"/knowledge/{KB}/files/{FILE}/remove", data={"expected_count": "1"})
    assert saved.status_code == 303
    assert pages.called("remove_file")[0] == (KB, FILE, RagActor(str(USER), FINANCE), 1)
    assert pages.module.checks[-1] == (KNOWLEDGE, Action.EDIT, Target(UUID(FINANCE)))


def test_add_files_validates_all_ids_and_checks_edit(pages: Pages) -> None:
    response = pages.client.post(f"/knowledge/{KB}/files", data={"file_ids": [FILE, SECOND_KB]})
    assert response.status_code == 303
    assert pages.called("add_files") == [(KB, [FILE, SECOND_KB], RagActor(str(USER), FINANCE))]
    assert pages.module.checks[-1][1] == Action.EDIT
    before = len(pages.calls)
    invalid = pages.client.post(f"/knowledge/{KB}/files", data={"file_ids": [FILE, "invalid"]})
    assert invalid.status_code == 404 and len(pages.calls) == before


def test_copy_route_uses_create_action_and_authenticated_actor(pages: Pages) -> None:
    response = pages.client.post(
        f"/knowledge/{KB}/copy", data={"title": "測試組合", "department_id": IT}
    )
    assert response.status_code == 303
    assert pages.called("copy_knowledge_base") == [(KB, "測試組合", RagActor(str(USER), FINANCE))]
    assert pages.module.checks[-1] == (KNOWLEDGE, Action.CREATE, Target(UUID(FINANCE)))


def test_empty_knowledge_base_omitted_from_department_and_it_choices(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = replace(KB_ROW, approval_status="approved", file_count=0)
    monkeypatch.setattr(
        rag_admin_service,
        "load_overview",
        lambda: rag_admin_service.KnowledgeOverview((empty,), ACCESS),
    )
    listing = pages.client.get("/knowledge")
    assert 'name="knowledge_base_id"' not in listing.text
    assert "部門指派（可多選）" in listing.text
    pages.is_it = True
    chat = pages.client.get("/knowledge/chat")
    assert f'name="knowledge_base_id" value="{KB}"' not in chat.text


def test_global_file_readable_but_other_department_has_no_write_controls(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign = replace(TXT_FILE, owner_department_id=IT)
    monkeypatch.setattr(
        rag_admin_service,
        "load_file_library",
        lambda: rag_admin_service.FileLibrary((foreign,), {FILE: (KB_ROW,)}, {}),
    )
    response = pages.client.get("/knowledge/files")
    assert response.status_code == 200 and "作業要點" in response.text
    assert f"/knowledge/files/{FILE}/edit" not in response.text
    assert "資訊部" in response.text


def test_upload_failure_fragment_is_escaped_and_not_marked_success(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        rag_admin_service,
        "upload_documents",
        lambda *args: [
            rag_admin_service.UploadOutcome("x.txt", False, "既有檔案「<b>來源</b>」請改用加入關聯")
        ],
    )
    response = pages.client.post(
        "/knowledge/files/upload",
        data={"source_type": "other", "effective_date_status": "unknown"},
        files={"files": ("x.txt", b"x", "text/plain")},
    )
    assert response.status_code == 400
    assert "&lt;b&gt;來源&lt;/b&gt;" in response.text and "<b>來源</b>" not in response.text
    assert "請改用加入關聯" in response.text
    page = pages.client.get("/knowledge/files")
    assert "hx-on::before-swap=" in page.text and "event.detail.shouldSwap = true" in page.text


def modal_markup(html: str, dialog_id: str) -> str:
    match = re.search(rf'<dialog\b[^>]*\bid="{re.escape(dialog_id)}".*?</dialog>', html, re.S)
    assert match is not None
    return match.group()


@pytest.mark.parametrize(
    "path,status",
    [(f"/knowledge/files/{FILE}/edit", 404), (f"/knowledge/{KB}/files/{FILE}/remove", 405)],
)
def test_removed_confirmation_and_name_pages_are_gone(pages: Pages, path: str, status: int) -> None:
    response = pages.client.get(path)
    assert response.status_code == status and pages.calls == []


def test_regular_removal_modal_shows_remaining_count_and_post_target(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    kb = replace(KB_ROW, file_count=3)
    files = tuple(
        replace(TXT_FILE, rag_file_id=f"{i:08d}-2222-4222-8222-222222222222") for i in range(3)
    )
    monkeypatch.setattr(
        rag_admin_service,
        "load_documents",
        lambda *args: rag_admin_service.DocumentsView(kb, ACCESS, files, {}),
    )
    response = pages.client.get(f"/knowledge/{KB}/documents")
    modal = modal_markup(response.text, "remove-file-" + files[0].rag_file_id)
    assert "移除後將剩 2 份檔案" in modal and "最後一份" not in modal
    assert f'action="/knowledge/{KB}/files/{files[0].rag_file_id}/remove"' in modal
    assert 'name="expected_count" value="3"' in modal
    assert 'method="post"' in modal and "data-close-dialog" in modal
    assert "hx-post" not in modal


@pytest.mark.parametrize("manager", [False, True])
@pytest.mark.parametrize("can_delete", [False, True])
def test_kb_delete_dialog_requires_management_and_delete_permission(
    pages: Pages, monkeypatch: pytest.MonkeyPatch, manager: bool, can_delete: bool
) -> None:
    pages.can_delete = can_delete
    kb = replace(KB_ROW, submitted_department_id=FINANCE if manager else IT)
    monkeypatch.setattr(
        rag_admin_service, "load_overview", lambda: rag_admin_service.KnowledgeOverview((kb,), ())
    )
    response = pages.client.get("/knowledge")
    shown = f'data-open-dialog="delete-kb-{KB}"' in response.text
    assert shown is (manager and can_delete)
    if shown:
        modal = modal_markup(response.text, "delete-kb-" + KB)
        assert "只移除知識庫、開放部門與檔案關聯，檔案和索引都保留" in modal
        assert f'action="/knowledge/{KB}/delete"' in modal


@pytest.mark.parametrize("path", [f"/knowledge/{KB}/delete", f"/knowledge/files/{FILE}/delete"])
def test_no_delete_permission_rejects_direct_post(pages: Pages, path: str) -> None:
    pages.can_delete = False
    pages.module.delete_allowed = False
    assert pages.client.post(path).status_code == 403
    assert pages.calls == []


def test_kb_delete_returns_list_for_success_and_rule_error(
    pages: Pages, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = pages.client.post(f"/knowledge/{KB}/delete")
    assert response.status_code == 303 and response.headers["location"] == "/knowledge"

    def blocked(*args: Any) -> None:
        raise RagRuleError("NOT_MANAGER", "目前部門不能管理此知識庫")

    monkeypatch.setattr(rag_admin_service, "delete_knowledge_base", blocked)
    response = pages.client.post(f"/knowledge/{KB}/delete")
    assert response.status_code == 400 and "<html" in response.text
    assert (
        "<h2>全部門知識庫管理</h2>" in response.text and "目前部門不能管理此知識庫" in response.text
    )


@pytest.mark.parametrize(
    "owned,is_it,can_delete",
    [(True, False, True), (True, False, False), (False, False, True), (False, True, True)],
)
def test_file_dialog_permissions_are_independent_of_kb_management(
    pages: Pages, monkeypatch: pytest.MonkeyPatch, owned: bool, is_it: bool, can_delete: bool
) -> None:
    pages.is_it = is_it
    pages.can_delete = can_delete
    file = replace(TXT_FILE, owner_department_id=FINANCE if owned else IT)
    monkeypatch.setattr(
        rag_admin_service,
        "load_file_library",
        lambda: rag_admin_service.FileLibrary((file,), {}, {}),
    )
    response = pages.client.get("/knowledge/files")
    assert (f'data-open-dialog="rename-file-{FILE}"' in response.text) is (owned or is_it)
    assert (f'data-open-dialog="delete-file-{FILE}"' in response.text) is (
        (owned or is_it) and can_delete
    )


@pytest.mark.parametrize("referenced", [False, True])
def test_file_delete_modal_blocks_confirmation_when_referenced(
    pages: Pages, monkeypatch: pytest.MonkeyPatch, referenced: bool
) -> None:
    pages.is_it = True
    references = {FILE: (KB_ROW,)} if referenced else {}
    monkeypatch.setattr(
        rag_admin_service,
        "load_file_library",
        lambda: rag_admin_service.FileLibrary((TXT_FILE,), references, {}),
    )
    response = pages.client.get("/knowledge/files")
    modal = modal_markup(response.text, "delete-file-" + FILE)
    assert ("請先移除關聯" in modal) is referenced
    assert ('type="submit"' in modal) is not referenced
    if referenced:
        assert KB_ROW.knowledge_name in modal
    rename = modal_markup(response.text, "rename-file-" + FILE)
    assert f'action="/knowledge/files/{FILE}/rename"' in rename
    assert f'value="{TXT_FILE.title}"' in rename and 'name="title"' in rename


@pytest.mark.parametrize("suffix,data", [("rename", {"title": "新檔名"}), ("delete", {})])
def test_file_modal_posts_redirect_to_library(
    pages: Pages, suffix: str, data: dict[str, str]
) -> None:
    response = pages.client.post(f"/knowledge/files/{FILE}/{suffix}", data=data)
    assert response.status_code == 303 and response.headers["location"] == "/knowledge/files"
