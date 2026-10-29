"""ADMIN 登入 UI 及四入口封閉驗收；合成 B／G 接點，不覆寫配置閘門。"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.constants import SESSION_COOKIE_NAME
from app.core.config import settings
from app.main import app
from app.schemas.e_access import AccessSnapshot, EffectiveDepartment
from app.schemas.e_admin import (
    Action,
    DataScope,
    Decision,
    Identity,
    IssuedSession,
    UnauthenticatedError,
)
from app.services import account_management_service, department_service, e_function_settings_service
from app.services.e_admin_service import EModule, get_e_module
from app.services.e_registry import REGISTRY, Capability

BASE = "http://invoiceguard-ai-admin.localhost:8080"
ORIGIN = {"Origin": BASE}
ACTOR = UUID("00000000-0000-4000-8000-000000000001")
DEPT = UUID("00000000-0000-4000-8000-000000000002")
SESSION = UUID("00000000-0000-4000-8000-000000000003")
TOKEN = "synthetic-admin-session-not-a-real-token"
POSTS = [
    f"/department/{DEPT}/edit",
    "/accounts/new",
    f"/accounts/{ACTOR}/edit",
    f"/accounts/{ACTOR}/disable",
    f"/accounts/{ACTOR}/departments",
    "/department",
    f"/department/{DEPT}/name",
    f"/department/{DEPT}/code",
    f"/department/{DEPT}/status",
    "/functions",
    f"/functions/{ACTOR}",
    f"/functions/{ACTOR}/status",
    f"/department-functions/{DEPT}/preview",
    f"/department-functions/{DEPT}/draft",
    f"/department-functions/{DEPT}",
]
GETS = [
    f"/department/{DEPT}/edit",
    "/accounts",
    "/accounts/new",
    f"/accounts/{ACTOR}/edit",
    "/department",
    "/functions",
    "/functions/new",
    f"/functions/{ACTOR}/edit",
    "/department-functions",
    f"/department-functions?department_id={DEPT}",
]


class Inputs(HTMLParser):
    """檢查實際表單，不以文字搜尋誤認密碼已清空。"""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.fields: dict[str, dict[str, str | None]] = {}
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "input" and data.get("name"):
            self.fields[data["name"]] = data


class Access:
    """只供測試的權威投影；正式程式不得載入。"""

    def __init__(self) -> None:
        self.user = Identity(
            ACTOR, SESSION, DEPT, 1, datetime.now(UTC) + timedelta(hours=1), True, "admin", True
        )
        self.registry = REGISTRY + (
            Capability("sys.accounts", "帳號管理", "系統管理", "/accounts", frozenset(Action)),
        )
        self.effects: dict[tuple[str, Action], str] = {}
        self.scope = DataScope("company")
        self.calls: list[tuple[str, Action]] = []

    def authenticate(self, token: SecretStr) -> Identity | None:
        return self.user if token.get_secret_value() == TOKEN else None

    def decide(self, user: Identity, code: str, action: Action) -> Decision:
        self.calls.append((code, action))
        return Decision(
            user.user_id,
            user.session_id,
            user.selected_department_id,
            code,
            action,
            "allow",
            True,
            "synthetic-policy",
            datetime.now(UTC) + timedelta(minutes=2),
            self.scope,
            department_effect=self.effects.get((code, action), "allow"),
        )

    def effective_access(self, user: Identity) -> AccessSnapshot:
        return AccessSnapshot(
            user.user_id,
            user.session_id,
            user.selected_department_id,
            "synthetic-policy",
            datetime.now(UTC) + timedelta(minutes=2),
            (EffectiveDepartment(DEPT, "SYNTHETIC", "測試部門"),),
            tuple(
                self.decide(user, item.code, action)
                for item in self.registry
                for action in item.actions
            ),
            True,
        )


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """真實路由與閘門，無預設 Cookie 或授權 override。"""
    # 明確驗證關閉初始化例外後的正式閘門；開啟模式另由 test_e_local_setup 驗證。
    from app.routers import e_admin

    monkeypatch.setattr(e_admin, "LOCAL_SETUP_ENABLED", False)
    with TestClient(
        app, base_url=BASE, client=("127.0.0.1", 50000), follow_redirects=False
    ) as client:
        yield client


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> EModule:
    """只組裝合成 B／G，保留配置頁的 pending 整合拒絕。"""
    auth = MagicMock()
    auth.login.return_value = IssuedSession(ACTOR, SecretStr(TOKEN))
    access = Access()
    value = EModule(access=access, auth=auth, registry=access.registry)
    monkeypatch.setitem(app.dependency_overrides, get_e_module, lambda: value)
    return value


@pytest.fixture
def stores(monkeypatch: pytest.MonkeyPatch) -> list[MagicMock]:
    """任何配置資料讀寫皆使測試立即失敗。"""
    spies = []
    for source, names in [
        (
            account_management_service,
            [
                "list_accounts",
                "get_account",
                "create_account",
                "update_account",
                "disable_account",
                "get_department_settings",
                "save_department_settings",
            ],
        ),
        (department_service, ["search", "create", "rename", "change_code", "set_active"]),
        (
            e_function_settings_service,
            [
                "search_functions",
                "get_function",
                "save_function",
                "change_function_status",
                "read_matrix",
                "review_matrix",
                "save_matrix",
            ],
        ),
    ]:
        for name in names:
            spy = MagicMock(side_effect=AssertionError("配置閘門未完成，不得碰資料"))
            monkeypatch.setattr(source, name, spy)
            spies.append(spy)
    return spies


def test_login_ui_loads_without_b_and_has_no_demo_identity(browser: TestClient) -> None:
    page = browser.get("/login")
    assert page.status_code == 200 and "e-login.css" in page.text
    fields = Inputs(page.text).fields
    assert fields["email"]["type"] == "email" and fields["email"]["autocomplete"] == "username"
    assert (
        fields["password"]["autocomplete"] == "current-password"
        and "value" not in fields["password"]
    )
    assert "忘記密碼" in page.text and "登入系統" in page.text
    for forbidden in [
        "sessionStorage",
        "localStorage",
        "1234",
        "5678",
        "ig_user",
        "USERS",
        "工程師：admin",
    ]:
        assert forbidden not in page.text
    assert page.headers["cache-control"] == "no-store" and "noindex" in page.headers["x-robots-tag"]


def test_responsive_styles_are_page_scoped(browser: TestClient) -> None:
    css = browser.get("/static/css/pages/e-login.css")
    assert css.status_code == 200
    assert "linear-gradient" in css.text and "@media" in css.text
    assert "100svh" in css.text and "min-width: 0" in css.text
    assert "@import" not in css.text and "http" not in css.text


def test_missing_b_renders_503_inside_login_without_secret_echo(browser: TestClient) -> None:
    response = browser.post(
        "/login",
        headers=ORIGIN,
        data={"email": "synthetic@example.invalid", "password": "synthetic-private-password"},
    )
    assert response.status_code == 503 and "e-login__card" in response.text
    assert Inputs(response.text).fields["email"]["value"] == "synthetic@example.invalid"
    assert (
        "synthetic-private-password" not in response.text and "set-cookie" not in response.headers
    )


def test_invalid_credentials_keep_only_email_and_generic_error(
    browser: TestClient, module: EModule
) -> None:
    module.auth.login.side_effect = UnauthenticatedError()
    page = browser.post(
        "/login",
        headers=ORIGIN,
        data={"email": "synthetic@example.invalid", "password": "synthetic-private-password"},
    )
    assert page.status_code == 401 and "帳號或密碼不正確" in page.text
    assert (
        "synthetic-private-password" not in page.text
        and "value" not in Inputs(page.text).fields["password"]
    )
    assert "set-cookie" not in page.headers


def test_login_success_keeps_shared_session_cookie_properties(
    browser: TestClient, module: EModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    page = browser.post(
        "/login",
        headers=ORIGIN,
        data={
            "email": "synthetic@example.invalid",
            "password": "synthetic-password",
            "next": "/functions",
        },
    )
    assert page.status_code == 303 and page.headers["location"] == "/welcome"
    cookie = page.headers["set-cookie"]
    for item in ["HttpOnly", "Secure", "SameSite=lax", "Path=/"]:
        assert item in cookie
    for item in ["Max-Age", "expires=", "Domain="]:
        assert item.lower() not in cookie.lower()


@pytest.mark.parametrize(
    "extra",
    [
        {"role": "admin"},
        {"user_id": str(ACTOR)},
        {"password": ""},
        {"password": "x" * 1025},
        {"email": "invalid"},
        {"email": "x" * 321},
        {"next": "x" * 201},
    ],
)
def test_login_rejects_invalid_or_unapproved_fields_before_b(
    browser: TestClient, module: EModule, extra: dict[str, str]
) -> None:
    page = browser.post(
        "/login",
        headers=ORIGIN,
        data={
            "email": "synthetic@example.invalid",
            "password": "synthetic-private-password",
            **extra,
        },
    )
    assert page.status_code == 400 and "synthetic-private-password" not in page.text
    module.auth.login.assert_not_called()


def test_login_rejects_duplicate_fields_and_cross_origin(
    browser: TestClient, module: EModule
) -> None:
    page = browser.post(
        "/login",
        headers={**ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
        content="email=a%40invalid&email=b%40invalid&password=test",
    )
    assert page.status_code == 400
    assert (
        browser.post(
            "/login",
            headers={"Origin": "http://evil.invalid"},
            data={"email": "synthetic@example.invalid", "password": "synthetic-password"},
        ).status_code
        == 403
    )
    module.auth.login.assert_not_called()


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.invalid",
        "//evil.invalid",
        "/functions?next=https://evil.invalid",
        "/accounts/../dev/accounts",
    ],
)
def test_next_never_redirects_to_external_or_dev_pages(browser: TestClient, target: str) -> None:
    page = browser.get("/login", params={"next": target})
    assert Inputs(page.text).fields["next"]["value"] == "/welcome"


@pytest.mark.parametrize(
    "method,path", [("GET", path) for path in GETS] + [("POST", path) for path in POSTS]
)
def test_all_configuration_routes_require_login_before_data(
    browser: TestClient, stores: list[MagicMock], method: str, path: str
) -> None:
    page = browser.request(
        method, path, headers={**ORIGIN, "X-Role": "admin", "X-User-ID": str(ACTOR)}
    )
    assert page.status_code == 303 and page.headers["location"].startswith("/login?")
    for spy in stores:
        spy.assert_not_called()


@pytest.mark.parametrize(
    "path", ["/accounts", "/department", "/functions", "/department-functions"]
)
def test_invalid_cookie_cannot_bypass_login(
    browser: TestClient, stores: list[MagicMock], path: str
) -> None:
    browser.cookies.set(SESSION_COOKIE_NAME, TOKEN)
    assert browser.get(path).status_code == 303
    for spy in stores:
        spy.assert_not_called()


@pytest.mark.parametrize(
    "path", ["/accounts", "/department", "/functions", "/department-functions"]
)
def test_authenticated_user_still_waits_for_safe_data_and_transaction_bridge(
    browser: TestClient, module: EModule, stores: list[MagicMock], path: str
) -> None:
    browser.cookies.set(SESSION_COOKIE_NAME, TOKEN)
    assert browser.get(path).status_code == 503
    for spy in stores:
        spy.assert_not_called()


@pytest.mark.parametrize("fault", ["frontend", "expired", "not_admin"])
def test_invalid_admin_qualification_never_reaches_features(
    browser: TestClient, module: EModule, stores: list[MagicMock], fault: str
) -> None:
    changes = {
        "frontend": {"session_audience": "frontend"},
        "expired": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        "not_admin": {"admin_access": False},
    }
    module.access.user = replace(module.access.user, **changes[fault])
    browser.cookies.set(SESSION_COOKIE_NAME, TOKEN)
    page = browser.get("/functions")
    assert page.status_code == (303 if fault == "expired" else 403)
    for spy in stores:
        spy.assert_not_called()


def test_view_permission_cannot_modify_and_scope_cannot_cross_departments(
    browser: TestClient, module: EModule, stores: list[MagicMock]
) -> None:
    browser.cookies.set(SESSION_COOKIE_NAME, TOKEN)
    module.access.effects[("sys.features", Action.EDIT)] = "deny"
    assert browser.post(f"/functions/{ACTOR}/status", headers=ORIGIN).status_code == 403
    module.access.scope = DataScope("departments", frozenset({DEPT}))
    other = UUID("00000000-0000-4000-8000-000000000009")
    assert browser.get(f"/department-functions?department_id={other}").status_code == 403
    for spy in stores:
        spy.assert_not_called()


def test_revoked_session_and_changed_permissions_rechecked_each_request(
    browser: TestClient, module: EModule, stores: list[MagicMock]
) -> None:
    browser.cookies.set(SESSION_COOKIE_NAME, TOKEN)
    assert browser.get("/functions").status_code == 503
    module.access.effects[("sys.features", Action.VIEW)] = "deny"
    assert browser.get("/functions").status_code == 403
    browser.cookies.set(SESSION_COOKIE_NAME, "synthetic-revoked-token")
    assert browser.get("/functions").status_code == 303


def test_missing_origin_is_rejected_before_invalid_form_and_no_b_call(
    browser: TestClient, module: EModule
) -> None:
    response = browser.post("/login", data={"password": "synthetic-private-password"})
    assert response.status_code == 403
    assert "synthetic-private-password" not in response.text
    module.auth.login.assert_not_called()


def test_login_error_escapes_email_instead_of_rendering_markup(
    browser: TestClient, module: EModule
) -> None:
    module.auth.login.side_effect = UnauthenticatedError()
    page = browser.post(
        "/login",
        headers=ORIGIN,
        data={
            "email": "<script>synthetic()</script>@example.invalid",
            "password": "synthetic-private-password",
        },
    )
    assert page.status_code == 401 and "&lt;script&gt;" in page.text
    assert "<script>synthetic()" not in page.text and "synthetic-private-password" not in page.text


@pytest.mark.parametrize("effect", ["unset", "deny"])
def test_welcome_only_login_does_not_unlock_other_features(
    browser: TestClient,
    module: EModule,
    stores: list[MagicMock],
    effect: str,
) -> None:
    module.access.user = replace(module.access.user, system_management=False)
    module.access.effects = {
        (item.code, action): effect for item in module.registry for action in item.actions
    }
    response = browser.post(
        "/login",
        headers=ORIGIN,
        data={
            "email": "synthetic@example.invalid",
            "password": "synthetic-not-real",
            "next": "/functions",
        },
    )
    assert response.status_code == 303 and response.headers["location"] == "/welcome"
    page = browser.get("/welcome")
    assert page.status_code == 200 and "歡迎回來" in page.text
    for path in ["/accounts", "/department", "/functions", "/workspace", "/account"]:
        assert f'href="{path}"' not in page.text
        assert browser.get(path).status_code == 403
    for spy in stores:
        spy.assert_not_called()


@pytest.mark.parametrize("action", [Action.CREATE, Action.EDIT, Action.DELETE])
def test_builtin_welcome_rejects_writes_even_for_a_system_manager(
    module: EModule, action: Action
) -> None:
    from app.schemas.e_admin import DeniedError

    with pytest.raises(DeniedError):
        module.check(module.access.user, "welcome", action)


@pytest.mark.parametrize("method", ["post", "patch", "delete"])
def test_welcome_has_no_content_write_endpoint(browser: TestClient, method: str) -> None:
    assert getattr(browser, method)("/welcome", headers=ORIGIN).status_code == 405


@pytest.mark.parametrize("fault", ["expired", "frontend", "not_admin", "revoked"])
def test_welcome_still_requires_current_valid_admin_identity(
    browser: TestClient, module: EModule, fault: str
) -> None:
    browser.cookies.set(SESSION_COOKIE_NAME, TOKEN)
    changes = {
        "expired": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        "frontend": {"session_audience": "frontend"},
        "not_admin": {"admin_access": False},
    }
    if fault == "revoked":
        browser.cookies.set(SESSION_COOKIE_NAME, "synthetic-revoked-token")
    else:
        module.access.user = replace(module.access.user, **changes[fault])
    assert browser.get("/welcome").status_code == (303 if fault in {"expired", "revoked"} else 403)


def test_formal_welcome_is_not_an_anonymous_public_page(browser: TestClient) -> None:
    assert browser.get("/welcome").status_code == 303
    assert browser.get("/").headers["location"] == "/welcome"


def test_welcome_baseline_does_not_depend_on_business_grant_service(
    browser: TestClient, module: EModule, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.schemas.e_admin import UnavailableError

    browser.cookies.set(SESSION_COOKIE_NAME, TOKEN)
    monkeypatch.setattr(module.access, "effective_access", MagicMock(side_effect=UnavailableError))
    page = browser.get("/welcome")
    assert page.status_code == 200 and "歡迎回來" in page.text
    assert 'href="/functions"' not in page.text
    assert browser.get("/functions").status_code == 503


@pytest.mark.parametrize("department_id", [DEPT, UUID("00000000-0000-4000-8000-000000000099")])
def test_baseline_follows_verified_selected_department_without_seed(
    module: EModule, department_id: UUID
) -> None:
    user = replace(
        module.access.user, selected_department_id=department_id, system_management=False
    )
    decision = module.check(user, "welcome", Action.VIEW)
    assert decision.selected_department_id == department_id
    assert decision.action == Action.VIEW and decision.scope.kind == "own"
    assert decision.policy_version == "builtin-welcome/v1"
    assert module.access.calls == []


def test_welcome_cannot_authorize_other_data_or_forged_write_capabilities(module: EModule) -> None:
    from app.schemas.e_admin import DeniedError, Target
    from app.services.e_authorization import require_permission
    from app.services.e_registry import Capability

    with pytest.raises(DeniedError):
        module.check(module.access.user, "welcome", Action.VIEW, Target(global_resource=True))
    with pytest.raises(DeniedError):
        require_permission(
            module.access,
            module.access.user,
            "welcome",
            Action.EDIT,
            definition=Capability(
                "welcome", "偽造修改", "測試", "/welcome", frozenset({Action.EDIT}), False, False
            ),
        )


def test_login_department_permission_changes_are_rechecked_with_same_cookie(
    browser: TestClient, module: EModule, stores: list[MagicMock], monkeypatch: pytest.MonkeyPatch
) -> None:
    """一般部門成員登入後，撤銷勾選即失去管理功能；歡迎頁仍可讀。"""
    module.access.user = replace(module.access.user, system_management=False)
    response = browser.post(
        "/login",
        headers=ORIGIN,
        data={"email": "synthetic@example.invalid", "password": "synthetic-not-real"},
    )
    assert response.status_code == 303 and response.headers["location"] == "/welcome"
    assert 'href="/system/features"' in browser.get("/welcome").text
    assert browser.get("/functions").status_code == 503  # 此替身沒有交易接點
    module.access.effects = {
        (item.code, action): "deny" for item in module.registry for action in item.actions
    }
    page = browser.get("/welcome")
    assert page.status_code == 200 and 'href="/system/features"' not in page.text
    assert browser.get("/functions").status_code == 403
    monkeypatch.setattr(module.access, "authenticate", lambda token: None)
    assert browser.get("/welcome").status_code == 303
    for spy in stores:
        spy.assert_not_called()


def test_department_view_checkbox_cannot_submit_unchecked_edits(
    browser: TestClient, module: EModule, stores: list[MagicMock]
) -> None:
    module.access.user = replace(module.access.user, system_management=False)
    module.access.effects = {
        (item.code, action): "allow" if action == Action.VIEW else "deny"
        for item in module.registry
        for action in item.actions
    }
    response = browser.post(
        "/login",
        headers=ORIGIN,
        data={"email": "synthetic@example.invalid", "password": "synthetic-not-real"},
    )
    assert response.status_code == 303
    assert browser.get("/welcome").status_code == 200
    assert browser.post(f"/functions/{ACTOR}/status", headers=ORIGIN).status_code == 403
    for spy in stores:
        spy.assert_not_called()
