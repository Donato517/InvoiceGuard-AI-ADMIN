# ruff: noqa: F811
"""正式供應商驗權、交易及畫面回歸；合成 Session／DB，不呼叫正式服務。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from test_department_login import (  # noqa: F401
    BASE,
    ORIGIN,
    USER,
    Database,
    SidebarNavigation,
    browser,
    encoded_password,
    login,
    module,
    state,
)

from app.db.repositories import suppliers_repository as repository
from app.schemas.e_admin import Action, UnavailableError
from app.schemas.supplier import SupplierInput
from app.services import supplier_management_service as service
from app.services.e_configuration_security import configuration_active
from app.services.e_registry import supported_actions

KEY = UUID("00000000-0000-4000-8000-000000000099")
STAMP = datetime(2026, 11, 4, tzinfo=UTC)
DATA = {
    "supplier_name": "合成測試供應商",
    "country_code": "TW",
    "tax_id": "00000101",
    "payment_terms": "net",
    "payment_basis": "invoice",
    "payment_days": "30",
    "payment_method": "transfer",
    "currency": "TWD",
}


@pytest.fixture
def suppliers(state: dict, monkeypatch: pytest.MonkeyPatch) -> dict:
    row = dict(
        state["functions"][0],
        function_code="suppliers",
        function_name="供應商管理",
        app_scope="admin",
        can_delete=True,
    )
    state["functions"].append(row)
    monkeypatch.setattr(repository, "open_session", lambda: Database(state))
    monkeypatch.setattr(repository, "prepare", lambda db: None)
    sample = SupplierInput.model_validate(DATA).model_dump() | {
        "supplier_id": KEY,
        "is_active": True,
        "created_at": STAMP,
        "updated_at": STAMP,
    }
    monkeypatch.setattr(repository, "list_suppliers", lambda db, q, offset: ([sample.copy()], 1))
    monkeypatch.setattr(repository, "get_supplier", lambda db, key: sample.copy())
    state["supplier_writes"] = []

    def create(db, values, actor):
        state["supplier_writes"].append((values, actor))
        return KEY

    monkeypatch.setattr(repository, "create_supplier", create)
    monkeypatch.setattr(repository, "update_supplier", lambda *args: True)
    monkeypatch.setattr(
        repository, "set_status", lambda *args: state["supplier_writes"].append(args) or True
    )
    return row


def test_company_master_registered_with_independent_actions() -> None:
    assert supported_actions("suppliers") == frozenset(Action)


def test_supplier_requires_session(browser: TestClient, suppliers: dict) -> None:
    response = browser.get("/suppliers")
    assert response.status_code == 303 and response.headers["location"].startswith("/login")


def test_real_navigation_and_pages(browser: TestClient, suppliers: dict) -> None:
    login(browser)
    for path in (
        "/welcome",
        "/suppliers",
        "/suppliers/new",
        f"/suppliers/{KEY}",
        f"/suppliers/{KEY}/edit",
    ):
        response = browser.get(path)
        assert response.status_code == 200
        entries = SidebarNavigation(response.text).entries
        # 2026-11-05 使用者核定側欄順序：供應商排在公司基本資料之後、發票功能之前（不再緊貼登出）
        assert entries.count("/suppliers") == 1
        assert entries.index("/suppliers") < entries.index("/logout")
        if "/company-profile" in entries:
            assert entries.index("/company-profile") + 1 == entries.index("/suppliers")
        if "/invoices/upload" in entries:
            assert entries.index("/suppliers") < entries.index("/invoices/upload")
        if path != "/welcome":
            assert "模擬模式" not in response.text
            assert "供應商功能列表" not in response.text
            assert "8092" not in response.text
            assert "{{ csrf }}" not in response.text


@pytest.mark.parametrize(
    "flag,path,method",
    [
        ("can_view", "/suppliers", "get"),
        ("can_create", "/suppliers/new", "get"),
        ("can_create", "/suppliers", "post"),
        ("can_edit", f"/suppliers/{KEY}/edit", "get"),
        ("can_edit", f"/suppliers/{KEY}", "post"),
        ("can_delete", f"/suppliers/{KEY}/status", "post"),
    ],
)
def test_each_permission_is_enforced(
    browser: TestClient, suppliers: dict, state: dict, flag: str, path: str, method: str
) -> None:
    suppliers[flag] = False
    login(browser)
    assert getattr(browser, method)(path, headers=ORIGIN).status_code == 403
    assert state["supplier_writes"] == []


def test_write_only_grant_does_not_grant_view(
    browser: TestClient, suppliers: dict, state: dict
) -> None:
    suppliers["can_view"] = False
    login(browser)
    assert browser.get("/suppliers").status_code == 403
    assert browser.get("/suppliers/new").status_code == 200
    response = browser.post("/suppliers", headers=ORIGIN, data=DATA)
    assert response.status_code == 200 and "已新增供應商" in response.text
    assert state["supplier_writes"][0][1] == USER
    assert any(sql.startswith("LOCK TABLE") for sql, _ in state["queries"])
    assert not configuration_active()


@pytest.mark.parametrize(
    "origin", [None, "https://external.invalid", "https://invoiceguard-ai-admin.localhost:8080"]
)
def test_cross_origin_or_missing_origin_rejected(
    browser: TestClient, suppliers: dict, state: dict, origin: str | None
) -> None:
    login(browser)
    assert (
        browser.post(
            "/suppliers", headers={"Origin": origin} if origin else {}, data=DATA
        ).status_code
        == 403
    )
    assert state["supplier_writes"] == []


def test_validation_duplicate_and_conflict(
    browser: TestClient, suppliers: dict, state: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(browser)
    assert (
        browser.post("/suppliers", headers=ORIGIN, data=DATA | {"supplier_name": ""}).status_code
        == 400
    )
    assert state["supplier_writes"] == []

    def duplicate(*args):
        cause = SimpleNamespace(
            sqlstate="23505", diag=SimpleNamespace(constraint_name="ux_suppliers_country_tax")
        )
        raise IntegrityError("private", {}, cause)

    monkeypatch.setattr(repository, "create_supplier", duplicate)
    response = browser.post("/suppliers", headers=ORIGIN, data=DATA)
    assert (
        response.status_code == 400
        and "相同統編" in response.text
        and "private" not in response.text
    )
    monkeypatch.setattr(repository, "update_supplier", lambda *args: False)
    response = browser.post(
        f"/suppliers/{KEY}", headers=ORIGIN, data=DATA | {"updated_at": STAMP.isoformat()}
    )
    assert response.status_code == 400 and "其他人修改" in response.text


def test_disable_confirmation_then_enable(
    browser: TestClient, suppliers: dict, state: dict
) -> None:
    login(browser)
    path = f"/suppliers/{KEY}/status"
    values = {"active": "0", "updated_at": STAMP.isoformat()}
    assert browser.post(path, headers=ORIGIN, data=values).status_code == 400
    assert state["supplier_writes"] == []
    assert browser.post(path, headers=ORIGIN, data=values | {"confirm": "yes"}).status_code == 303
    assert browser.post(path, headers=ORIGIN, data=values | {"active": "1"}).status_code == 303
    assert [args[2] for args in state["supplier_writes"]] == [False, True]


def test_permission_revocation_during_transaction(
    browser: TestClient, suppliers: dict, state: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(browser)

    def revoke(db):
        suppliers["can_create"] = False

    monkeypatch.setattr(repository, "prepare", revoke)
    assert browser.post("/suppliers", headers=ORIGIN, data=DATA).status_code == 403
    assert state["supplier_writes"] == []


def test_service_without_verified_context_fails_closed() -> None:
    with pytest.raises(UnavailableError):
        service.list_suppliers("", 1)


def test_database_failure_is_sanitized(
    browser: TestClient, suppliers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(browser)

    def failure(*args):
        raise RuntimeError("private-connection-secret")

    monkeypatch.setattr(repository, "list_suppliers", failure)
    response = browser.get("/suppliers")
    assert response.status_code == 503 and "private-connection-secret" not in response.text
