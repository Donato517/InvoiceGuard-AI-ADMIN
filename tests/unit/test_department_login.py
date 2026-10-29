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
    assert navigation.entries == ["/welcome", "/logout"]
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
        assert "(21)三聯式統一發票" in sidebar and "尚未接線" in sidebar
        assert 'href="/invoices"' not in sidebar
        assert 'aria-disabled="true"' in sidebar
        navigation = SidebarNavigation(response.text)
        assert navigation.entries[navigation.entries.index("/accounts") + 1] == "/logout"
    row = state["functions"][-1]
    row.update(can_view=False, can_create=False, can_edit=False, can_delete=False)
    assert "(21)三聯式統一發票" not in browser.get("/welcome").text
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
