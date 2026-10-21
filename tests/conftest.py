"""
pytest 共用 fixture。
規則：
    - 單元測試不連資料庫：以 app.dependency_overrides 替換登入與資料庫相依，以 monkeypatch 替換 service。
    - 整合測試（需要測試資料庫）放 tests/integration/，DATABASE_URL 不可指向正式環境。
"""

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.core.deps import require_admin
from app.db.session import get_db
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """未登入的瀏覽器。follow_redirects=False 以便檢查導向。"""
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def admin_client() -> Iterator[TestClient]:
    """已登入管理者的瀏覽器：略過登入檢查，資料庫 Session 以 None 代替。"""
    fake_admin = SimpleNamespace(id=1, display_name="測試管理者", role="admin", is_active=True)

    def fake_require_admin(request: Request) -> SimpleNamespace:
        request.state.admin = fake_admin
        return fake_admin

    app.dependency_overrides[require_admin] = fake_require_admin
    app.dependency_overrides[get_db] = lambda: None
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.clear()
