"""
模組名稱：tests.unit.test_auth_offline
功能說明：測試後台管理者的登入與登出流程（POST /login、導向 /dashboard、POST /logout）。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.constants import SESSION_COOKIE_NAME
from app.core import deps
from app.core.deps import get_db
from app.core.security import hash_password
from app.db.repositories import user_repository
from app.main import app

client = TestClient(app, follow_redirects=False)


@pytest.fixture(autouse=True)
def mock_admin_in_db(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_db = MagicMock()

    def fake_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = fake_get_db
    app.dependency_overrides[deps.get_optional_db] = fake_get_db

    admin_user = SimpleNamespace(
        id=1,
        email="admin@example.com",
        password_hash=hash_password("password"),
        display_name="系統管理員",
        role="admin",
        is_active=True,
        last_login_at=None,
    )

    monkeypatch.setattr(
        user_repository,
        "get_by_email",
        lambda db, email: admin_user if email.strip().lower() == "admin@example.com" else None,
    )
    monkeypatch.setattr(
        user_repository,
        "get_by_id",
        lambda db, user_id: admin_user if user_id == 1 else None,
    )

    yield
    app.dependency_overrides.clear()


def test_admin_login_success() -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "password", "next": "/dashboard"},
        headers={"Host": "testserver", "Origin": "http://testserver"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert SESSION_COOKIE_NAME in response.cookies


def test_admin_login_and_access_dashboard() -> None:
    login_resp = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "password"},
        headers={"Host": "testserver", "Origin": "http://testserver"},
    )
    assert login_resp.status_code == 303
    cookie = login_resp.cookies.get(SESSION_COOKIE_NAME)
    assert cookie is not None

    dash_resp = client.get(
        "/dashboard",
        cookies={SESSION_COOKIE_NAME: cookie},
    )
    assert dash_resp.status_code == 200
    assert "儀表板" in dash_resp.text or "今日上傳" in dash_resp.text


def test_admin_login_wrong_password_returns_400() -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "wrong_password"},
        headers={"Host": "testserver", "Origin": "http://testserver"},
    )
    assert response.status_code == 400
    assert "帳號或密碼錯誤" in response.text


def test_admin_logout() -> None:
    response = client.post(
        "/logout",
        headers={"Host": "testserver", "Origin": "http://testserver"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
