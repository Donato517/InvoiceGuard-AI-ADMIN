"""
模組名稱：tests.unit.test_auth_offline
功能說明：測試後台管理者的離線／示範登入流程（POST /login、導向 /dashboard、POST /logout）。
"""

from fastapi.testclient import TestClient

from app.constants import SESSION_COOKIE_NAME
from app.main import app

client = TestClient(app, follow_redirects=False)


def test_admin_demo_login_success() -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "password", "next": "/dashboard"},
        headers={"Host": "testserver", "Origin": "http://testserver"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert SESSION_COOKIE_NAME in response.cookies


def test_admin_demo_login_and_access_dashboard() -> None:
    # 登入取得 cookie
    login_resp = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "password"},
        headers={"Host": "testserver", "Origin": "http://testserver"},
    )
    assert login_resp.status_code == 303
    cookie = login_resp.cookies.get(SESSION_COOKIE_NAME)
    assert cookie is not None

    # 帶著 cookie 訪問 /dashboard
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
