"""核定本機初始化驗收；保留真實路由與來源閘門，只替換資料來源。"""

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.routers import e_admin
from app.schemas.e_function_settings import FunctionListPage
from app.services import account_management_service, department_service, e_function_settings_service

BASE = "http://invoiceguard-ai-admin.localhost:8080"
PATHS = [
    "/welcome",
    "/functions",
    "/functions/new",
    "/department",
    "/accounts",
]


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """開啟核定模式，以合成空資料證明不需 Cookie 即可到服務層。"""
    monkeypatch.setattr(e_admin, "LOCAL_SETUP_ENABLED", True)
    monkeypatch.setattr(settings, "APP_ENV", "local")
    monkeypatch.setattr(settings, "VERCEL", "")
    monkeypatch.setattr(settings, "MODULE_BACKEND", "remote")
    monkeypatch.setattr(department_service, "search", MagicMock(return_value=[]))
    monkeypatch.setattr(
        account_management_service, "list_accounts", MagicMock(return_value=([], 0))
    )
    monkeypatch.setattr(
        e_function_settings_service,
        "search_functions",
        lambda query: FunctionListPage(query, (), 0, 1),
    )
    with TestClient(
        app, base_url=BASE, client=("127.0.0.1", 50000), follow_redirects=False
    ) as client:
        yield client


@pytest.mark.parametrize("path", PATHS)
def test_local_setup_opens_without_identity(browser: TestClient, path: str) -> None:
    response = browser.get(path)
    assert response.status_code == 200
    assert ("歡迎回來" if path == "/welcome" else "本機初始化設定") in response.text
    assert "set-cookie" not in response.headers
    assert not browser.cookies


def test_welcome_is_a_simple_greeting_without_setup_details(browser: TestClient) -> None:
    page = browser.get("/welcome")
    assert "歡迎回來" in page.text
    assert "願今天的工作一切順心" in page.text
    for text in ("department_permissions", "本機初始化設定", "正式 DB", "設定步驟", "待接線"):
        assert text not in page.text
    assert "今日上傳" not in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "noindex" in page.headers["x-robots-tag"]
    assert department_service.search.call_count == 0
    assert account_management_service.list_accounts.call_count == 0
    assert 'href="/dashboard"' not in page.text


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("restriction", ["cloud", "peer", "host", "backend"])
def test_setup_never_opens_outside_local_boundary(
    browser: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    restriction: str,
) -> None:
    base = "https://invoiceguard-ai-admin.vercel.app" if restriction == "host" else BASE
    peer = "192.0.2.1" if restriction == "peer" else "127.0.0.1"
    if restriction == "cloud":
        monkeypatch.setattr(settings, "APP_ENV", "production")
        monkeypatch.setattr(settings, "VERCEL", "1")
    if restriction == "backend":
        monkeypatch.setattr(settings, "MODULE_BACKEND", "cloud")
    with TestClient(app, base_url=base, client=(peer, 50000), follow_redirects=False) as client:
        expected = 303 if path == "/welcome" and restriction == "cloud" else 404
        assert client.get(path).status_code == expected
    assert department_service.search.call_count == 0
    assert account_management_service.list_accounts.call_count == 0


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": "https://untrusted.invalid"}, {"Origin": BASE, "Sec-Fetch-Site": "cross-site"}],
)
def test_write_still_requires_origin_before_services(
    browser: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    write = MagicMock(side_effect=AssertionError("拒絕的來源不可寫入"))
    monkeypatch.setattr(e_function_settings_service, "save_function", write)
    assert browser.post("/functions", data={}, headers=headers).status_code == 403
    write.assert_not_called()


def test_trusted_write_reaches_validation_not_login(browser: TestClient) -> None:
    response = browser.post("/functions", data={}, headers={"Origin": BASE})
    assert response.status_code == 400
    assert "location" not in response.headers


def test_setup_does_not_open_formal_workspace_or_issue_session(browser: TestClient) -> None:
    response = browser.get("/workspace")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?")
    assert browser.get("/dashboard").status_code == 303
    login = browser.get("/login")
    assert 'href="/welcome"' in login.text
    response = browser.post(
        "/login",
        data={"email": "synthetic@example.invalid", "password": "synthetic-not-real"},
        headers={"Origin": BASE},
    )
    assert response.status_code == 503
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("path", PATHS)
def test_disabling_setup_restores_fail_closed_guard(
    browser: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setattr(e_admin, "LOCAL_SETUP_ENABLED", False)
    response = browser.get(path)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?")
    assert 'href="/welcome"' not in browser.get("/login").text
    assert department_service.search.call_count == 0
