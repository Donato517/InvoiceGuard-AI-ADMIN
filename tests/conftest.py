"""
pytest 共用 fixture。
規則：
    - 單元測試不連資料庫：以 app.dependency_overrides 替換登入與資料庫相依，以 monkeypatch 替換 service。
    - 整合測試（需要測試資料庫）放 tests/integration/，DATABASE_URL 不可指向正式環境。
"""

import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from network_guard import install_network_block

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
    # AI 模組設定（與 API 相同，2026-10-29）
    "GEMINI_MODEL",
    "GROQ_API_KEY",
    "GROQ_DEFAULT_MODEL",
    "GROQ_ADVANCED_MODEL",
    "GOOGLE_CLOUD_PROJECT_ID",
    "GOOGLE_CLOUD_LOCATION",
    "DOCUMENT_AI_PROCESSOR_ID",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "VERTEX_EMBEDDING_MODEL",
    "VERTEX_EMBEDDING_DIMENSIONS",
    "DB_POOL_MODE",
    "DB_SSL_MODE",
    "DB_CONNECT_TIMEOUT_SECONDS",
}
for _name in tuple(os.environ):
    if _name.upper() in _CONFIGURATION_NAMES:
        del os.environ[_name]


@pytest.fixture(autouse=True, scope="session")
def isolate_local_session_key() -> Iterator[None]:
    """本機簽章金鑰只留在記憶體：路徑無法讀寫，不碰開發者實際的 tmp/，也不依賴暫存目錄。"""
    from app.services import admin_login_service

    path = Path(os.devnull) / "local-session-key"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(admin_login_service, "_local_session_key_path", lambda: path)
        admin_login_service._local_session_key.cache_clear()
        yield
    admin_login_service._local_session_key.cache_clear()


@pytest.fixture(autouse=True)
def block_external_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """單元測試禁止網路、SQLAlchemy 連線與直接 psycopg 連線（實作見 tests/network_guard.py）。"""
    install_network_block(monkeypatch)


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


@pytest.fixture(autouse=True)
def seed_test_invoices_fixture() -> None:
    """單元測試自動注入測試發票資料，避免測試依賴空資料庫。"""
    import copy

    from app.routers import invoices as inv

    inv._INVOICE_STORE.clear()
    inv._INVOICE_STORE.extend(copy.deepcopy(inv.DEMO_TEST_INVOICES))
