"""測試目標：頁面路由、管理者登入保護、錯誤頁、靜態檔"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.constants import SESSION_COOKIE_NAME
from app.core import deps
from app.core.config import settings
from app.core.security import create_session_token
from app.db.session import get_db
from app.main import app
from app.services import system_service


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_page_renders(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert "InvoiceGuard 後台" in response.text


def test_static_css_is_served(client: TestClient) -> None:
    response = client.get("/static/css/main.css")

    assert response.status_code == 200


def test_robots_txt_blocks_indexing(client: TestClient) -> None:
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert "Disallow: /" in response.text


def test_dashboard_redirects_to_login_without_session(client: TestClient) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fdashboard"


def test_tampered_session_cookie_redirects_to_login(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test-secret-key-for-unit-tests")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(1) + "tampered")

    response = client.get("/dashboard")

    assert response.status_code == 303


def test_non_admin_user_gets_forbidden(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    normal_user = SimpleNamespace(id=2, display_name="一般使用者", role="user", is_active=True)
    monkeypatch.setattr(deps.user_repository, "get_by_id", lambda db, user_id: normal_user)
    app.dependency_overrides[deps.get_session_user_id] = lambda: 2
    app.dependency_overrides[get_db] = lambda: None
    try:
        response = client.get("/dashboard")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert "沒有後台權限" in response.text


def test_dashboard_renders_for_admin(admin_client: TestClient) -> None:
    response = admin_client.get("/dashboard")

    assert response.status_code == 200
    assert "測試管理者" in response.text


def test_job_runs_page_lists_records(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = SimpleNamespace(
        job_name="sync_einvoice",
        trigger="cron",
        status="failed",
        started_at=datetime(2026, 10, 20, 18, 0, tzinfo=UTC),
        finished_at=datetime(2026, 10, 20, 18, 1, tzinfo=UTC),
        summary=None,
        error_message="財政部平台逾時",
    )

    def fake_list_job_runs(db: Any, *, job_name: str | None = None) -> list[SimpleNamespace]:
        return [run]

    monkeypatch.setattr(system_service, "list_job_runs", fake_list_job_runs)

    response = admin_client.get("/system/job-runs")

    assert response.status_code == 200
    assert "同步財政部電子發票" in response.text
    assert "2026/10/21 02:00" in response.text  # UTC 18:00 → 台灣時間隔天 02:00
    assert "財政部平台逾時" in response.text


def test_unknown_page_shows_custom_404(client: TestClient) -> None:
    response = client.get("/not-exists")

    assert response.status_code == 404
    assert "找不到頁面" in response.text


def test_login_rejects_cross_site_post(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "x"},
        headers={"Origin": "https://evil.example.com"},
    )

    assert response.status_code == 403
