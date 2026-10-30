"""登入整合：實際 SQL Repository、網站 Session、HTTP guard；僅合成資料、不連正式 DB。"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.constants import SESSION_COOKIE_NAME
from app.core.security import hash_password
from app.db.repositories import e_function_settings_repository
from app.main import app
from app.routers import e_admin
from app.schemas.e_admin import Action, UnauthenticatedError, UnavailableError
from app.services import e_function_settings_service
from app.services.admin_login_service import DepartmentLoginAdapter, _serializer
from app.services.e_admin_service import EModule, get_e_module

USER = UUID("00000000-0000-4000-8000-000000000011")
DEPARTMENT = UUID("00000000-0000-4000-8000-000000000012")
OTHER = UUID("00000000-0000-4000-8000-000000000013")
BASE = "http://invoiceguard-ai-admin.localhost:8080"
ORIGIN = {"Origin": BASE}
EMAIL = "synthetic@example.invalid"
PASSWORD = "synthetic-only-not-a-real-password"


class SidebarNavigation(HTMLParser):
    """讀實際側欄順序及登出表單，不把頁首按鈕當成側欄項目。"""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.inside = False
        self.entries: list[str] = []
        self.logout_methods: list[str] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "nav" and attributes.get("aria-label") == "後台選單":
            self.inside = True
        elif self.inside and tag == "a":
            self.entries.append(attributes.get("href", ""))
        elif self.inside and tag == "form" and attributes.get("action") == "/logout":
            self.entries.append("/logout")
            self.logout_methods.append(attributes.get("method", "").lower())

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav":
            self.inside = False


class Result:
    """SQLAlchemy 結果替身；SQL 本身仍由真正 repository 建構。"""

    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def mappings(self) -> "Result":
        return self

    def one_or_none(self) -> Any:
        return self.values[0] if self.values else None

    def one(self) -> Any:
        assert len(self.values) == 1
        return self.values[0]

    def scalar_one(self) -> Any:
        return self.one()

    def scalar_one_or_none(self) -> Any:
        return self.one_or_none()

    def __iter__(self) -> Iterator[Any]:
        return iter(self.values)


class Database:
    """只模擬必要 SQL 的結果與交易，不是可供正式程式使用的假 DB。"""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.pending_login = False

    def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> Result:
        sql = str(statement)
        values = parameters or {}
        self.state["queries"].append((sql, values))
        if sql.startswith("SET ") or sql.startswith("LOCK TABLE"):
            return Result([])
        if "pg_catalog.row_security_active" in sql:
            return Result([False])
        if "SET last_login_at=" in sql:
            self.pending_login = True
            return Result([USER])
        if "SET primary_department_id=" in sql:
            self.state["account"]["primary_department_id"] = values["department_id"]
            return Result([USER])
        if "FROM invoiceguard_identity.user_accounts a" in sql:
            if values.get("email", EMAIL) != EMAIL or values.get("user_id", USER) != USER:
                return Result([])
            return Result([dict(self.state["account"])])
        if "CROSS JOIN public.app_functions" in sql:
            if not self.state["membership"]:
                return Result([])
            if "department_functions" in self.state:
                return Result(
                    list(self.state["department_functions"].get(values["department_id"], []))
                )
            if values["department_id"] != DEPARTMENT:
                return Result([])
            return Result(list(self.state["functions"]))
        if "SELECT d.department_id" in sql:
            if not self.state["membership"]:
                return Result([])
            if "departments" in self.state:
                return Result(list(self.state["departments"]))
            return Result(
                [
                    {
                        "department_id": DEPARTMENT,
                        "department_code": "TEST",
                        "department_name": "測試部門",
                        "is_active": True,
                    }
                ]
            )
        raise AssertionError("未預期 SQL；測試不允許新增資料表、角色、授權或 DB Session")

    def commit(self) -> None:
        self.state["commits"] += 1
        if self.pending_login:
            self.state["login_updates"] += 1
        self.pending_login = False

    def rollback(self) -> None:
        self.pending_login = False

    def close(self) -> None:
        self.state["closes"] += 1


@pytest.fixture(scope="module")
def encoded_password() -> str:
    return hash_password(PASSWORD)


@pytest.fixture
def state(encoded_password: str) -> dict[str, Any]:
    return {
        "account": {
            "user_id": USER,
            "email": EMAIL,
            "password_hash": encoded_password,
            "account_status": "active",
            "credential_version": 1,
            "locked_until": None,
            "primary_department_id": DEPARTMENT,
            "profile_active": True,
            "resigned_at": None,
        },
        "membership": True,
        "queries": [],
        "commits": 0,
        "login_updates": 0,
        "closes": 0,
        "functions": [
            dict(
                function_code=code,
                function_name=code,
                app_scope="both",
                is_active=True,
                can_view=True,
                can_create=True,
                can_edit=True,
                can_delete=False,
            )
            for code in ["accounts", "department", "functions", "future-feature"]
        ],
    }


@pytest.fixture
def module(state: dict[str, Any]) -> EModule:
    registry = get_e_module().registry
    adapter = DepartmentLoginAdapter(registry, lambda: Database(state))
    return EModule(access=adapter, auth=adapter, registry=registry)


@pytest.fixture
def browser(module: EModule, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # 真正關閉本機免登入；只換 DB Session 工廠，不覆寫權限 guard。
    monkeypatch.setattr(e_admin, "LOCAL_SETUP_ENABLED", False)
    monkeypatch.setitem(app.dependency_overrides, get_e_module, lambda: module)
    with TestClient(
        app, base_url=BASE, client=("127.0.0.1", 50000), follow_redirects=False
    ) as client:
        yield client


def login(browser: TestClient) -> None:
    response = browser.post("/login", headers=ORIGIN, data={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 303 and response.headers["location"] == "/welcome"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Max-Age" not in cookie and "expires=" not in cookie.lower()


def test_login_composes_session_and_only_updates_login_record(
    browser: TestClient, module: EModule, state: dict[str, Any]
) -> None:
    login(browser)
    data = module.auth.session_data(SecretStr(browser.cookies.get(SESSION_COOKIE_NAME)))
    assert data["uid"] == str(USER) and data["department"] == str(DEPARTMENT)
    assert data["functions"]["accounts"] == 7 and data["functions"]["future-feature"] == 7
    assert "password_hash" not in data and "role" not in data
    assert state["login_updates"] == 1 and state["commits"] == 1
    writes = [
        sql
        for sql, _ in state["queries"]
        if sql.lstrip().startswith(("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"))
    ]
    assert len(writes) == 1 and "SET last_login_at=" in writes[0]
    page = browser.get("/welcome")
    assert page.status_code == 200 and 'href="/functions"' in page.text
    assert 'href="/accounts"' in page.text
    assert state["login_updates"] == 1


def rag_manager_row(state: dict[str, Any], **flags: bool) -> None:
    state["functions"].append(
        {
            **state["functions"][0],
            "function_code": "rag_manager",
            "function_name": "RAG知識庫管理",
            "app_scope": "admin",
            **flags,
        }
    )


def test_rag_manager_catalog_row_opens_knowledge_page(
    browser: TestClient, state: dict[str, Any]
) -> None:
    rag_manager_row(state)
    login(browser)

    assert 'href="/knowledge"' in browser.get("/welcome").text
    page = browser.get("/knowledge")

    assert page.status_code == 200 and "知識庫管理" in page.text


@pytest.mark.parametrize("grant", [False, True], ids=["no-catalog-row", "view-not-granted"])
def test_knowledge_page_requires_rag_manager_view(
    browser: TestClient, state: dict[str, Any], grant: bool
) -> None:
    if grant:
        rag_manager_row(state, can_view=False)
    login(browser)

    assert 'href="/knowledge"' not in browser.get("/welcome").text
    assert browser.get("/knowledge").status_code == 403


@pytest.mark.parametrize(
    "fault", ["wrong_password", "unknown", "disabled", "locked", "resigned", "no_department"]
)
def test_rejected_login_never_writes(
    browser: TestClient, state: dict[str, Any], fault: str
) -> None:
    email, password = EMAIL, PASSWORD
    if fault == "wrong_password":
        password = "synthetic-wrong-password"
    elif fault == "unknown":
        email = "unknown@example.invalid"
    elif fault == "disabled":
        state["account"]["account_status"] = "disabled"
    elif fault == "locked":
        state["account"]["locked_until"] = datetime.now(UTC) + timedelta(minutes=1)
    elif fault == "resigned":
        state["account"]["resigned_at"] = datetime.now(UTC)
    else:
        state["membership"] = False
    page = browser.post("/login", headers=ORIGIN, data={"email": email, "password": password})
    assert page.status_code == 401 and "帳號或密碼不正確" in page.text
    assert "set-cookie" not in page.headers and state["commits"] == 0
    assert not any(
        sql.lstrip().startswith(("UPDATE", "INSERT", "DELETE")) for sql, _ in state["queries"]
    )


def test_no_functions_can_still_log_in_to_welcome_only(
    browser: TestClient, state: dict[str, Any]
) -> None:
    state["functions"] = []
    login(browser)
    page = browser.get("/welcome")
    assert page.status_code == 200 and "歡迎回來" in page.text
    navigation = SidebarNavigation(page.text)
    assert navigation.entries == ["/welcome", "/logout"]
    assert navigation.logout_methods == ["post"]
    assert 'href="/functions"' not in page.text
    assert browser.get("/functions").status_code == 403


def test_real_configuration_route_uses_department_flag_without_a_role(
    browser: TestClient, module: EModule, state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    login(browser)
    monkeypatch.setattr(e_function_settings_repository, "open_session", lambda: Database(state))
    monkeypatch.setattr(e_function_settings_repository, "count_functions", lambda db, query: 0)
    monkeypatch.setattr(e_function_settings_repository, "page_functions", lambda db, query: ())
    page = browser.get("/functions")
    assert page.status_code == 200 and "新增功能" in page.text
    navigation = SidebarNavigation(page.text)
    assert navigation.entries[navigation.entries.index("/accounts") + 1] == "/logout"
    assert navigation.logout_methods == ["post"]
    assert any(sql.startswith("LOCK TABLE") for sql, _ in state["queries"])
    # 同 Cookie，改勾選後立即拒絕，不能靠 Session 舊資料繼續操作。
    for row in state["functions"]:
        row.update(can_view=False, can_create=False, can_edit=False)
    assert browser.get("/functions").status_code == 403
    assert browser.get("/welcome").status_code == 200


def test_unchecked_edit_rejected_before_business_write(
    browser: TestClient, state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for row in state["functions"]:
        row["can_edit"] = False
    login(browser)
    calls = []
    monkeypatch.setattr(
        e_function_settings_service, "change_function_status", lambda *args: calls.append(args)
    )
    assert browser.post(f"/functions/{USER}/status", headers=ORIGIN).status_code == 403
    assert calls == []


@pytest.mark.parametrize(
    "fault", ["tampered", "expired", "credential_changed", "membership_removed"]
)
def test_session_validation_rejects_invalid_state(
    browser: TestClient, module: EModule, state: dict[str, Any], fault: str
) -> None:
    login(browser)
    token = browser.cookies.get(SESSION_COOKIE_NAME)
    if fault == "tampered":
        browser.cookies.clear()
        browser.cookies.set(SESSION_COOKIE_NAME, token + "tampered")
    elif fault == "expired":
        data = module.auth.session_data(SecretStr(token))
        data["expires"] = int((datetime.now(UTC) - timedelta(seconds=1)).timestamp())
        browser.cookies.clear()
        browser.cookies.set(SESSION_COOKIE_NAME, _serializer().dumps(data))
    elif fault == "credential_changed":
        state["account"]["credential_version"] = 2
    else:
        state["membership"] = False
    assert browser.get("/welcome").status_code == 303


def test_logout_clears_website_cookie_without_database_updates(
    browser: TestClient, state: dict[str, Any]
) -> None:
    login(browser)
    navigation = SidebarNavigation(browser.get("/welcome").text)
    assert navigation.entries[navigation.entries.index("/accounts") + 1] == "/logout"
    assert navigation.logout_methods == ["post"]
    before = len(state["queries"])
    response = browser.post("/logout", headers=ORIGIN)
    assert response.status_code == 303 and "Max-Age=0" in response.headers["set-cookie"]
    assert len(state["queries"]) == before
    assert browser.get("/welcome").status_code == 303


def test_another_department_cannot_be_used_to_union_permissions(module: EModule) -> None:
    issued = module.login(EMAIL, SecretStr(PASSWORD))
    actor = module.identity(issued.token)
    with pytest.raises(UnauthenticatedError):
        module.access.decide(
            replace(actor, selected_department_id=OTHER), "sys.features", Action.EDIT
        )


def test_deployed_session_requires_existing_signing_configuration(
    module: EModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", SecretStr(""))
    with pytest.raises(UnavailableError):
        module.login(EMAIL, SecretStr(PASSWORD))


@pytest.mark.parametrize("fault", ["inactive", "frontend_only"])
def test_disabled_or_frontend_only_function_is_not_available(
    browser: TestClient, state: dict[str, Any], fault: str
) -> None:
    login(browser)
    for row in state["functions"]:
        if row["function_code"] == "functions":
            row["is_active"] = fault != "inactive"
            row["app_scope"] = "frontend" if fault == "frontend_only" else "both"
    assert browser.get("/functions").status_code == 403
    assert 'href="/functions"' not in browser.get("/welcome").text


def test_checked_create_does_not_require_unchecked_view(
    browser: TestClient, state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    for row in state["functions"]:
        row.update(can_view=False, can_create=True, can_edit=False, can_delete=False)
    login(browser)
    # 此新增表單不讀既有功能內容，僅驗新增；查看列表仍拒絕。
    assert browser.get("/functions/new").status_code == 200
    assert browser.get("/functions").status_code == 403


def test_removed_session_department_does_not_remove_remaining_primary_membership(
    browser: TestClient, module: EModule, state: dict[str, Any]
) -> None:
    """舊 Session 的部門被取消，重登採用仍有效的主要部門，不恢復舊管理許可。"""
    login(browser)
    old_data = module.auth.session_data(SecretStr(browser.cookies.get(SESSION_COOKIE_NAME)))
    # 模擬先前已選取、但現在已取消所屬關聯的部門；新主要部門仍在。
    old_data["department"] = str(OTHER)
    browser.cookies.clear()
    browser.cookies.set(
        SESSION_COOKIE_NAME,
        _serializer().dumps(old_data),
        domain="invoiceguard-ai-admin.localhost",
        path="/",
    )
    for row in state["functions"]:
        row.update(can_view=False, can_create=False, can_edit=False, can_delete=False)
    state["functions"].append(
        dict(
            function_code="Triplicate Uniform Invoice",
            function_name="三聯式統一發票",
            app_scope="both",
            is_active=True,
            can_view=True,
            can_create=True,
            can_edit=True,
            can_delete=True,
        )
    )
    assert state["account"]["primary_department_id"] == DEPARTMENT
    assert state["membership"] is True
    assert browser.get("/welcome").status_code == 303
    login(browser)
    data = module.auth.session_data(SecretStr(browser.cookies.get(SESSION_COOKIE_NAME)))
    assert data["department"] == str(DEPARTMENT)
    assert data["functions"] == {"Triplicate Uniform Invoice": 15}
    page = browser.get("/welcome")
    assert page.status_code == 200
    navigation = SidebarNavigation(page.text)
    assert navigation.entries == ["/welcome", "/invoices/upload", "/invoices", "/logout"]
    assert browser.get("/accounts").status_code == 403


def test_catalog_sidebar_tracks_live_department_grants(
    browser: TestClient, module: EModule, state: dict[str, Any]
) -> None:
    """任意新增目錄功能依部門勾選出現，撤銷即消失，不必重登且不增加資料寫入。"""
    login(browser)
    state["functions"].append(
        dict(
            function_code="Triplicate Uniform Invoice",
            function_name="(21)三聯式統一發票",
            app_scope="both",
            is_active=True,
            can_view=True,
            can_create=True,
            can_edit=True,
            can_delete=False,
        )
    )
    for url in ["/welcome", "/functions/new", "/workspace"]:
        response = browser.get(url)
        assert response.status_code == 200
        sidebar = response.text.split('<nav aria-label="後台選單">', 1)[1].split("</nav>", 1)[0]
        assert "發票上傳" in sidebar and 'href="/invoices"' in sidebar
        assert "future-feature" in sidebar and "尚未接線" in sidebar
        assert 'aria-disabled="true"' in sidebar
        navigation = SidebarNavigation(response.text)
        assert navigation.entries[navigation.entries.index("/accounts") + 1] == "/logout"
    row = state["functions"][-1]
    row.update(can_view=False, can_create=False, can_edit=False, can_delete=False)
    assert 'href="/invoices"' not in browser.get("/welcome").text
    assert state["login_updates"] == 1 and state["commits"] == 1
    assert not any("Triplicate Uniform Invoice" in sql for sql, _ in state["queries"])


@pytest.mark.parametrize("fault", ["inactive", "frontend_only", "no_grants", "membership_removed"])
def test_catalog_sidebar_hides_ineligible_functions(
    browser: TestClient, state: dict[str, Any], fault: str
) -> None:
    login(browser)
    row = state["functions"][-1]
    row["function_name"] = "不應出現的業務功能"
    if fault == "inactive":
        row["is_active"] = False
    elif fault == "frontend_only":
        row["app_scope"] = "frontend"
    elif fault == "no_grants":
        row.update(can_view=False, can_create=False, can_edit=False, can_delete=False)
    else:
        state["membership"] = False
    page = browser.get("/welcome")
    assert "不應出現的業務功能" not in page.text
    assert page.status_code == (303 if fault == "membership_removed" else 200)


def test_catalog_sidebar_does_not_turn_untrusted_codes_into_links(
    browser: TestClient, state: dict[str, Any]
) -> None:
    row = state["functions"][-1]
    row.update(
        function_code="https://example.invalid/execute", function_name="<script>bad()</script>"
    )
    login(browser)
    page = browser.get("/welcome")
    assert "&lt;script&gt;bad()&lt;/script&gt;" in page.text
    assert "<script>bad()</script>" not in page.text
    assert 'href="https://example.invalid/execute"' not in page.text
    assert state["commits"] == 1


def test_catalog_sidebar_shows_operation_grants_without_implying_view(
    browser: TestClient, state: dict[str, Any]
) -> None:
    for row in state["functions"]:
        if row["function_code"] == "functions":
            row.update(function_name="功能目錄合成名稱", can_view=False, can_create=True)
    login(browser)
    page = browser.get("/welcome")
    assert "功能目錄合成名稱" in page.text and "未授權查看" in page.text
    assert 'href="/functions"' not in page.text
    assert browser.get("/functions").status_code == 403
    assert browser.get("/functions/new").status_code == 200


def test_catalog_only_member_keeps_welcome_and_authorized_sidebar(
    browser: TestClient, state: dict[str, Any]
) -> None:
    for row in state["functions"][:-1]:
        row.update(can_view=False, can_create=False, can_edit=False, can_delete=False)
    state["functions"][-1]["function_name"] = "僅授權的業務功能"
    login(browser)
    page = browser.get("/welcome")
    assert page.status_code == 200 and "僅授權的業務功能" in page.text
    assert SidebarNavigation(page.text).entries == ["/welcome", "/logout"]
    assert browser.get("/accounts").status_code == 403


def test_header_switch_department_updates_primary_and_session(
    browser: TestClient, module: EModule, state: dict[str, Any]
) -> None:
    """頁首切換部門選單位於登出左側，切換後更新主要部門與網站 Session 並反映該部門選單。"""
    state["departments"] = [
        {
            "department_id": DEPARTMENT,
            "department_code": "TEST",
            "department_name": "測試部門",
            "is_active": True,
        },
        {
            "department_id": OTHER,
            "department_code": "FIN",
            "department_name": "財務部",
            "is_active": True,
        },
    ]
    state["department_functions"] = {
        DEPARTMENT: state["functions"],
        OTHER: [],
    }
    login(browser)
    welcome_page = browser.get("/welcome")
    assert welcome_page.status_code == 200
    assert 'action="/switch-department"' in welcome_page.text
    assert "切換部門" in welcome_page.text
    assert "FIN－財務部" in welcome_page.text
    assert welcome_page.text.index('action="/switch-department"') < welcome_page.text.index(
        'button type="submit" class="btn btn--secondary btn--small">登出'
    )

    switched = browser.post(
        "/switch-department",
        headers=ORIGIN,
        data={"switch_department_id": str(OTHER), "next": "/functions"},
    )
    assert switched.status_code == 303
    assert switched.headers["location"] == "/welcome"
    assert state["account"]["primary_department_id"] == OTHER
    data = module.auth.session_data(SecretStr(browser.cookies.get(SESSION_COOKIE_NAME)))
    assert data["department"] == str(OTHER)
    assert data["functions"] == {}

    after_page = browser.get("/welcome")
    assert after_page.status_code == 200
    assert SidebarNavigation(after_page.text).entries == ["/welcome", "/logout"]
    assert browser.get("/functions").status_code == 403


def test_department_status_requires_explicit_edit(
    browser: TestClient, state: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """登入但僅能查看部門時，列表開關仍無法繞過原有修改權限。"""
    from app.services import department_service

    for row in state["functions"]:
        if row["function_code"] == "department":
            row["can_edit"] = False
    calls = []
    monkeypatch.setattr(department_service, "set_active", lambda *args: calls.append(args))
    login(browser)
    response = browser.post(
        f"/department/{DEPARTMENT}/status", headers=ORIGIN, data={"active": "false"}
    )
    assert response.status_code == 403 and calls == []


def test_procurement_department_invoice_upload_landing_and_four_step_limit(
    browser: TestClient, state: dict[str, Any]
) -> None:
    """非財務部門僅顯示「📤 發票上傳」與「🗂️ 發票管理」且不顯示步驟 5～9；財務部額外開放財會檢核、申報資料、會計傳票及步驟 5～9。"""
    invoice_grant = [
        dict(
            function_code="Triplicate Uniform Invoice",
            function_name="(21)三聯式統一發票",
            app_scope="both",
            is_active=True,
            can_view=True,
            can_create=True,
            can_edit=True,
            can_delete=False,
        )
    ]
    state["departments"] = [
        {
            "department_id": DEPARTMENT,
            "department_code": "PROC",
            "department_name": "採購部",
            "is_active": True,
        },
        {
            "department_id": OTHER,
            "department_code": "FIN",
            "department_name": "財務部",
            "is_active": True,
        },
    ]
    state["functions"] = invoice_grant
    state["department_functions"] = {
        DEPARTMENT: invoice_grant,
        OTHER: invoice_grant,
    }

    from app.routers.invoices import _INVOICE_STORE

    orig_user = _INVOICE_STORE[0].get("uploaded_by")
    _INVOICE_STORE[0]["uploaded_by"] = str(state["account"]["user_id"])
    try:
        login(browser)
        welcome_page = browser.get("/welcome")
        assert welcome_page.status_code == 200
        sidebar = welcome_page.text.split('<nav aria-label="後台選單">', 1)[1].split("</nav>", 1)[0]
        assert "發票上傳" in sidebar and 'href="/invoices/upload"' in sidebar
        assert "發票管理" in sidebar and 'href="/invoices"' in sidebar
        assert "財會檢核" not in sidebar
        assert "申報資料" not in sidebar
        assert "會計傳票" not in sidebar

        list_page = browser.get("/invoices")
        assert list_page.status_code == 200
        assert "發票管理" in list_page.text
        assert "/invoices/1/flow?step=4" in list_page.text
        assert "/invoices/1/flow?step=9" not in list_page.text
        assert "前四步核對已完成／待財務接續" in list_page.text

        # 本人上傳發票可檢視；他人上傳之發票（如 invoice 2），非財務部無權檢視或操作
        assert "/invoices/2" not in list_page.text
        assert browser.get("/invoices/2").status_code == 403
        assert browser.get("/invoices/2/flow?step=4").status_code == 403

        # 採購核對修正（步驟 3）應具備左右分欄與 14 項核對欄位，無原始檔時提示明確訊息
        step3_page = browser.get("/invoices/1/flow?step=3")
        assert step3_page.status_code == 200
        assert "invoice-correction-split" in step3_page.text
        assert "correction-original-column" in step3_page.text
        assert "correction-field-column" in step3_page.text
        assert "舊資料只有檔名，沒有保存原始檔" in step3_page.text
        for expected_field_label in [
            "發票聯式／識別",
            "發票字軌號碼",
            "發票日期",
            "買受人名稱",
            "買受人統編",
            "品名／用途",
            "數量",
            "單價",
            "金額",
            "銷售額合計",
            "課稅別與稅額",
            "總計／國字大寫",
            "備註",
            "銷貨人",
        ]:
            assert expected_field_label in step3_page.text

        step4_page = browser.get("/invoices/1/flow?step=4")
        assert step4_page.status_code == 200
        assert "系統自動檢核" in step4_page.text
        assert "完成前四步，返回待處理發票" in step4_page.text
        for finance_step_label in [
            "財務審核",
            "產生申報檔",
            "匯入國稅局軟體",
            "自動產生傳票",
            "匯出結果",
        ]:
            assert finance_step_label not in step4_page.text

        for forbidden_route in ["/invoices/review", "/invoices/filing", "/invoices/voucher"]:
            assert browser.get(forbidden_route).status_code == 403
        for forbidden_step in range(5, 10):
            assert browser.get(f"/invoices/1/flow?step={forbidden_step}").status_code == 403
        assert (
            browser.post(
                "/invoices/1/flow/advance", headers=ORIGIN, data={"from_step": 4}
            ).status_code
            == 403
        )
        assert (
            browser.post(
                "/invoices/1/review", headers=ORIGIN, data={"decision": "passed"}
            ).status_code
            == 403
        )

        # 切換至財務部後，開放 🔍 財會檢核、📑 申報資料、🧾 會計傳票 與步驟 5～9
        browser.post(
            "/switch-department",
            headers=ORIGIN,
            data={"switch_department_id": str(OTHER), "next": "/invoices"},
        )
        fin_welcome = browser.get("/welcome")
        fin_sidebar = fin_welcome.text.split('<nav aria-label="後台選單">', 1)[1].split(
            "</nav>", 1
        )[0]
        for finance_tab in ["發票上傳", "發票管理", "財會檢核", "申報資料", "會計傳票"]:
            assert finance_tab in fin_sidebar
        assert browser.get("/invoices/review").status_code == 200
        assert browser.get("/invoices/filing").status_code == 200
        assert browser.get("/invoices/voucher").status_code == 200
        fin_step5 = browser.get("/invoices/1/flow?step=5")
        assert fin_step5.status_code == 200
        assert "財務審核" in fin_step5.text and "匯出結果" in fin_step5.text
        # 步驟 5 財務審核需顯示 14 項核對欄位，但不含發票原圖／PDF 原始文件
        assert "核對欄位（14 項）" in fin_step5.text
        assert "發票聯式／識別" in fin_step5.text
        assert "銷貨人" in fin_step5.text
        assert "invoice-file-preview" not in fin_step5.text

        # 財務部可檢視所有發票（包含他人上傳的發票）
        fin_list = browser.get("/invoices")
        assert fin_list.status_code == 200
        assert "/invoices/2" in fin_list.text

        # 步驟 6 產生申報檔：單張檢核 81 字元格式 21 預覽 + 批次產生申報檔卡片
        fin_step6 = browser.get("/invoices/1/flow?step=6")
        assert fin_step6.status_code == 200
        assert "本張發票檢核" in fin_step6.text
        assert "格式 21 單行預覽（81 字元" in fin_step6.text
        assert "批次產生申報檔" in fin_step6.text

        # 申報資料頁：顯示批次產生申報檔
        filing_page = browser.get("/invoices/filing")
        assert filing_page.status_code == 200
        assert "批次產生申報檔" in filing_page.text
        assert "已勾選" in filing_page.text
        assert "產生並下載申報檔" in filing_page.text

        # 批次匯出格式 21 申報檔：示範發票的統編與字軌未通過 F09 檢核，整批不產檔
        export_resp = browser.post(
            "/invoices/filing/export",
            headers=ORIGIN,
            data={"period": "11308", "invoice_ids": [1, 2]},
        )
        assert export_resp.status_code == 400
        assert "content-disposition" not in export_resp.headers
    finally:
        _INVOICE_STORE[0]["uploaded_by"] = orig_user


KNOWLEDGE_ROUTES = (
    ("GET", "/knowledge"),
    ("POST", "/knowledge/shared-tax/toggle-dept"),
    ("GET", "/knowledge/shared-tax/documents"),
    ("POST", "/knowledge/shared-tax/documents/sample-tax/toggle-dept"),
    ("POST", "/knowledge/shared-tax/documents/sample-tax/status"),
    ("GET", "/knowledge/shared-tax/edit"),
    ("POST", "/knowledge/shared-tax/rename"),
    ("POST", "/knowledge/shared-tax/delete"),
    ("GET", "/knowledge/shared-tax/documents/sample-tax/edit"),
    ("POST", "/knowledge/shared-tax/documents/sample-tax/rename"),
    ("POST", "/knowledge/shared-tax/documents/sample-tax/delete"),
    ("GET", "/knowledge/shared-tax/documents/sample-tax/content"),
    ("POST", "/knowledge/shared-tax/documents/sample-tax/content"),
    ("GET", "/knowledge/shared-tax/documents/sample-tax/download"),
    ("POST", "/knowledge/documents"),
)


@pytest.mark.parametrize("method,path", KNOWLEDGE_ROUTES)
def test_all_knowledge_routes_reject_without_admin_cookie_even_if_legacy_guard_is_overridden(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch, method: str, path: str
) -> None:
    """所有 GET/POST 都不可透過舊 require_admin override、Header 或 URL 自報身分。"""
    from urllib.parse import parse_qs, urlsplit

    from app.core import deps

    monkeypatch.setitem(app.dependency_overrides, deps.require_admin, lambda: None)
    response = browser.request(
        method,
        path,
        headers={**ORIGIN, "Authorization": "Bearer synthetic-session", "X-User-ID": str(USER)},
        params={"user_id": str(USER), "department_id": str(DEPARTMENT), "role": "admin"},
    )
    assert response.status_code == 303
    location = urlsplit(response.headers["location"])
    assert location.path == "/login"
    assert parse_qs(location.query)["next"][0].split("?", 1)[0] == path


@pytest.mark.parametrize("method,path", KNOWLEDGE_ROUTES)
def test_all_knowledge_routes_reject_logged_in_department_without_rag_permission(
    browser: TestClient, method: str, path: str
) -> None:
    """已登入但目前部門沒勾 rag_manager，所有 GET/POST 都是 403。"""
    login(browser)
    response = browser.request(method, path, headers=ORIGIN)
    assert response.status_code == 403
    assert not hasattr(response.context["request"].state, "knowledge_identity")
    assert not hasattr(response.context["request"].state, "e_configuration_authenticated")


@pytest.mark.parametrize("allowed", [False, True])
def test_rag_mapping_and_it_state_use_only_selected_department(
    browser: TestClient, state: dict[str, Any], module: EModule, allowed: bool
) -> None:
    """資訊部所屬身分不提升財務部權限，三個授權投影都使用 rag_manager 對應。"""
    from app.schemas.e_admin import Target
    from app.services.e_admin_service import KNOWLEDGE

    rag_manager_row(state)
    rag = dict(state["functions"][-1])
    common = list(state["functions"][:-1])
    state["departments"] = [
        {
            "department_id": DEPARTMENT,
            "department_code": "technology_d",
            "department_name": "資訊部",
        },
        {"department_id": OTHER, "department_code": "finance_d", "department_name": "財務部"},
    ]
    state["account"]["primary_department_id"] = OTHER
    state["department_functions"] = {
        DEPARTMENT: common + [rag],
        OTHER: common
        + [
            {
                **rag,
                "can_view": allowed,
                "can_create": False,
                "can_edit": False,
                "can_delete": False,
            }
        ],
    }
    login(browser)
    page = browser.get(
        "/knowledge",
        headers={"X-Department-ID": str(DEPARTMENT)},
        params={"department_id": str(DEPARTMENT)},
    )
    assert page.status_code == (200 if allowed else 403)
    identity = module.identity(SecretStr(browser.cookies.get(SESSION_COOKIE_NAME)))
    snapshot = module.access.effective_access(identity)
    rag_view = [
        item
        for item in snapshot.decisions
        if item.function_code == KNOWLEDGE and item.action == Action.VIEW
    ]
    assert len(rag_view) == 1 and rag_view[0].selected_department_id == OTHER
    assert rag_view[0].department_effect == ("allow" if allowed else "deny")
    sidebar = [item for item in module.sidebar_menu(identity) if item.code == KNOWLEDGE]
    assert all(item.route == ("/knowledge" if allowed else None) for item in sidebar)
    if allowed:
        decision = module.check(identity, KNOWLEDGE, Action.VIEW, Target(OTHER))
        assert decision.scope.department_ids == frozenset({OTHER})
        assert page.context["request"].state.is_it_workspace is False
        assert page.context["request"].state.header_selected_department_id == OTHER
        assert len(page.context["request"].state.header_departments) == 2
        assert 'name="switch_department_id"' in page.text
        assert "technology_d－資訊部" in page.text and "finance_d－財務部" in page.text
        assert any(
            item.code == KNOWLEDGE and item.can_open
            for item in module.workspace_access(identity).functions
        )
    else:
        assert not hasattr(page.context["request"].state, "knowledge_identity")


def test_rag_sidebar_keeps_existing_navigation_order(
    browser: TestClient, state: dict[str, Any]
) -> None:
    """新增 RAG 入口不重排既有發票／部門／功能／帳號與登出。"""
    state["functions"].append(
        {
            **state["functions"][0],
            "function_code": "Triplicate Uniform Invoice",
            "function_name": "發票上傳",
        }
    )
    login(browser)
    before = SidebarNavigation(browser.get("/welcome").text).entries
    rag_manager_row(state)
    after = SidebarNavigation(browser.get("/welcome").text).entries
    assert "/knowledge" in after
    assert [item for item in after if item != "/knowledge"] == before
    assert all(
        path in after
        for path in ("/invoices", "/accounts", "/department", "/functions", "/welcome")
    )


@pytest.mark.parametrize("change", [{"is_active": False}, {"app_scope": "frontend"}])
def test_disabled_or_wrong_scope_rag_catalog_cannot_open_knowledge(
    browser: TestClient, state: dict[str, Any], change: dict[str, Any]
) -> None:
    """停用或非 ADMIN 範圍的目錄功能不產生有效權限。"""
    rag_manager_row(state)
    state["functions"][-1].update(change)
    login(browser)
    assert browser.get("/knowledge").status_code == 403


def test_knowledge_router_covers_every_declared_get_and_post() -> None:
    """新路由若沒列入授權覆蓋或沒有 guard，就必須讓測試失敗。"""
    from fastapi.routing import APIRoute

    from app.routers import knowledge

    routes = [route for route in knowledge.router.routes if isinstance(route, APIRoute)]
    actual = {
        (method, route.path.replace("{kb_id}", "shared-tax").replace("{doc_id}", "sample-tax"))
        for route in routes
        for method in route.methods
        if method in {"GET", "POST"}
    }
    assert actual == set(KNOWLEDGE_ROUTES)
    assert all(
        any(item.dependency is knowledge.require_knowledge_access for item in route.dependencies)
        for route in routes
    )
