"""
pytest 共用 fixture。
規則：
    - 單元測試不連資料庫：以 app.dependency_overrides 替換登入與資料庫相依，以 monkeypatch 替換 service。
    - 整合測試（需要測試資料庫）放 tests/integration/，DATABASE_URL 不可指向正式環境。
"""

import os
import socket
from collections.abc import Iterator
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

# 測試程序不繼承公司機密；在測試模組匯入 app 前清除已知設定。
# 只檢查名稱並移除，不讀取、列印或保存原值；不影響父 PowerShell 程序。
# pydantic-settings 讀取環境變數不分大小寫，因此以大寫比對（Linux 上 database_url 也會被讀到）。
_CONFIGURATION_NAMES = {
    "APP_NAME",
    "LOCAL_SECRET_FIELDS",
    "SUPABASE_SERVICE_ROLE_KEY",
    "GOOGLE_API_KEY",
    "VERCEL_TOKEN",
    "DATABASE_URL_UNPOOLED",
    "JWT_SECRET_KEY",
    "CRON_SECRET",
    "AI_API_KEY",
    "EINVOICE_APP_ID",
    "EINVOICE_API_KEY",
    "BLOB_READ_WRITE_TOKEN",
    "APP_ENV",
    "DEBUG",
    "MODULE_BACKEND",
    "REMOTE_ADMIN_BASE_URL",
    "VERCEL",
    "VERCEL_ENV",
    "VERCEL_GIT_COMMIT_SHA",
    "DATABASE_URL",
    "SESSION_SECRET_KEY",
    "SESSION_MAX_AGE_SECONDS",
    "SUPABASE_URL",
    "SUPABASE_STORAGE_BUCKET",
    "SUPABASE_SECRET_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "DB_POOL_MODE",
    "DB_SSL_MODE",
    "DB_CONNECT_TIMEOUT_SECONDS",
}
for _name in tuple(os.environ):
    if _name.upper() in _CONFIGURATION_NAMES:
        del os.environ[_name]


@pytest.fixture(autouse=True)
def block_external_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """單元測試禁止網路、SQLAlchemy 連線與直接 psycopg 連線。"""
    import psycopg
    from sqlalchemy.engine import Engine

    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    inside_socketpair: ContextVar[bool] = ContextVar("inside_socketpair", default=False)

    def deny_connection(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("單元測試禁止外部網路或真實資料庫連線")

    def guarded_connect(connection: socket.socket, address: Any) -> Any:
        # Windows asyncio 以 socketpair 的 loopback 連線建立程序內喚醒通道。
        if inside_socketpair.get() and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(connection, address)
        return deny_connection()

    def guarded_socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
        token = inside_socketpair.set(True)
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            inside_socketpair.reset(token)

    monkeypatch.setattr(socket, "create_connection", deny_connection)
    monkeypatch.setattr(socket, "getaddrinfo", deny_connection)
    monkeypatch.setattr(socket, "socketpair", guarded_socketpair)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_connection)
    monkeypatch.setattr(socket.socket, "sendto", deny_connection)
    monkeypatch.setattr(Engine, "connect", deny_connection)
    monkeypatch.setattr(Engine, "raw_connection", deny_connection)
    monkeypatch.setattr(psycopg, "connect", deny_connection)
    monkeypatch.setattr(psycopg.Connection, "connect", deny_connection)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", deny_connection)


@pytest.fixture
def client() -> TestClient:
    """未登入的瀏覽器。follow_redirects=False 以便檢查導向。"""
    from app.main import app

    return TestClient(app, follow_redirects=False)


@pytest.fixture
def admin_client() -> Iterator[TestClient]:
    """已登入管理者的瀏覽器：略過登入檢查，資料庫 Session 以 None 代替。"""
    from app.core.deps import require_admin
    from app.db.session import get_db
    from app.main import app

    fake_admin = SimpleNamespace(id=1, display_name="測試管理者", role="admin", is_active=True)

    def fake_require_admin(request: Request) -> SimpleNamespace:
        request.state.admin = fake_admin
        return fake_admin

    app.dependency_overrides[require_admin] = fake_require_admin
    app.dependency_overrides[get_db] = lambda: None
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.clear()
