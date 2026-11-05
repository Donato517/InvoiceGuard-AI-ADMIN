# ruff: noqa: F811
"""正式供應商驗權、交易及畫面回歸；合成 Session／DB，不呼叫正式服務。"""

import logging
from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
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
from app.services import showcase_service
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


def test_showcase_explains_missing_department_grant_and_unavailable_store(
    browser: TestClient, suppliers: dict, state: dict, module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """供應商查看不會自動取得範例查看，補授權後能進入，缺資料服務另回 503。"""
    monkeypatch.setattr(showcase_service, "get_e_module", lambda: module)
    monkeypatch.setattr(showcase_service, "open_session", lambda: nullcontext(Database(state)))
    master_reads = []
    original = repository.get_supplier

    def read_master(db, key):
        master_reads.append(key)
        return original(db, key)

    monkeypatch.setattr(repository, "get_supplier", read_master)
    login(browser)
    assert browser.get("/suppliers").status_code == 200
    path = f"/showcases/by-supplier/{KEY}"
    denied = browser.get(path)
    assert denied.status_code == 403
    assert "目前部門尚未取得 Show Case 查看權限" in denied.text
    assert 'href="/suppliers"' in denied.text and not master_reads

    for unused_code in ("supplier.showcase", "snow_case"):
        state["functions"].append(suppliers | {"function_code": unused_code})
    assert browser.get(path).status_code == 403 and not master_reads

    grant = suppliers | {"function_code": "show_case", "is_active": False}
    state["functions"].append(grant)
    assert browser.get(path).status_code == 403 and not master_reads
    grant["is_active"] = True
    grant["can_view"] = False
    assert browser.get(path).status_code == 403 and not master_reads
    grant["can_view"] = True

    def unavailable(*args, **kwargs):
        raise UnavailableError

    monkeypatch.setattr(showcase_service.v2, "supplier_examples", unavailable)
    missing = browser.get(path)
    assert missing.status_code == 503 and "Show Case 資料服務暫時無法使用" in missing.text
    monkeypatch.setattr(showcase_service.v2, "supplier_examples", lambda *args, **kwargs: [])
    monkeypatch.setattr(showcase_service.repository, "archive_writable", lambda db: False)
    opened = browser.get(path)
    assert opened.status_code == 200 and "合成測試供應商" in opened.text
    assert "目前沒有範例" in opened.text and "啟用歷程保存後" in opened.text
    assert "加入範例發票</a>" not in opened.text
    monkeypatch.setattr(showcase_service.repository, "archive_writable", lambda db: True)
    assert "加入範例發票</a>" in browser.get(path).text
    assert 'href="/showcases"' not in browser.get("/suppliers").text


UNSUPPORTED = "目前尚未支援此國別的發票範例"
UNAVAILABLE = "Show Case 資料服務暫時無法使用"
PRIVATE_VALUES = ("private-sql", "private-param", "private-dsn", "42P01", "42501", "08006")


@pytest.fixture
def showcase_page(browser, suppliers, state, module, monkeypatch):
    """保留真實路由與授權，只替換資料連線。"""
    monkeypatch.setattr(showcase_service, "get_e_module", lambda: module)
    monkeypatch.setattr(showcase_service, "open_session", lambda: nullcontext(Database(state)))
    grant = suppliers | {"function_code": "show_case", "is_active": True}
    state["functions"].append(grant)
    login(browser)
    return browser, grant


def master_as(monkeypatch: pytest.MonkeyPatch, country: str) -> None:
    master = repository.get_supplier(None, KEY) | {"country_code": country}
    monkeypatch.setattr(repository, "get_supplier", lambda db, key: master.copy())


def failing_read(sqlstate: str, calls: list[str] | None = None):
    """模擬 PostgreSQL 真實錯誤；訊息與參數是合成私密值，不能出現在畫面或日誌。"""

    def read(*args, **kwargs):
        if calls is not None:
            calls.append(sqlstate)
        raise ProgrammingError(
            "private-sql", {"p": "private-param"}, SimpleNamespace(sqlstate=sqlstate)
        )

    return read


def assert_private_values_hidden(response, caplog: pytest.LogCaptureFixture) -> None:
    for value in PRIVATE_VALUES:
        assert value not in response.text
        assert value not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_showcase_foreign_supplier_is_unsupported_without_archive_reads(
    showcase_page, state: dict, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """JP 供應商不讀不存在的歷程表（42P01），顯示尚未支援而不是 503。"""
    browser, _ = showcase_page
    master_as(monkeypatch, "JP")
    calls: list[str] = []
    for owner, name in (
        (showcase_service.repository, "search"),
        (showcase_service.repository, "archive_writable"),
        (showcase_service.v2, "supplier_examples"),
    ):
        monkeypatch.setattr(owner, name, failing_read("42P01", calls))
    caplog.set_level(logging.DEBUG)
    response = browser.get(f"/showcases/by-supplier/{KEY}")
    assert response.status_code == 200
    assert UNSUPPORTED in response.text and "合成測試供應商" in response.text
    assert UNAVAILABLE not in response.text and "加入範例發票" not in response.text
    assert "目前沒有範例" not in response.text
    assert calls == []
    assert not any(
        "invoiceguard_showcase" in sql or "supplier_invoice_examples" in sql
        for sql, _ in state["queries"]
    )
    assert_private_values_hidden(response, caplog)


@pytest.mark.parametrize("case", ["missing", "show_case_view_denied", "suppliers_view_denied"])
def test_showcase_foreign_supplier_keeps_404_and_403(
    showcase_page, suppliers: dict, state: dict, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    browser, grant = showcase_page
    foreign = repository.get_supplier(None, KEY) | {"country_code": "JP"}
    master_reads: list[UUID] = []

    def lookup(db, key):
        master_reads.append(key)
        return None if case == "missing" else foreign.copy()

    monkeypatch.setattr(repository, "get_supplier", lookup)
    for owner, name in (
        (showcase_service.repository, "search"),
        (showcase_service.v2, "supplier_examples"),
    ):
        monkeypatch.setattr(owner, name, failing_read("42P01"))
    if case == "show_case_view_denied":
        grant["can_view"] = False
    if case == "suppliers_view_denied":
        suppliers["can_view"] = False
    response = browser.get(f"/showcases/by-supplier/{uuid4() if case == 'missing' else KEY}")
    assert response.status_code == (404 if case == "missing" else 403)
    assert UNSUPPORTED not in response.text and UNAVAILABLE not in response.text
    assert bool(master_reads) == (case == "missing")
    assert not any("invoiceguard_showcase" in sql for sql, _ in state["queries"])


@pytest.mark.parametrize("has_sample", [True, False])
def test_showcase_taiwan_supplier_keeps_v2_gallery(
    showcase_page, monkeypatch: pytest.MonkeyPatch, has_sample: bool
) -> None:
    browser, _ = showcase_page
    master_as(monkeypatch, "TW")
    item = {
        "id": KEY,
        "payload": {
            "title": "合成三聯式範例",
            "revisions": [
                {"id": str(uuid4()), "status": "draft", "layout": "三聯式", "writing": ""}
            ],
        },
    }
    legacy: list[str] = []
    monkeypatch.setattr(showcase_service.repository, "search", failing_read("42P01", legacy))
    monkeypatch.setattr(
        showcase_service.v2, "supplier_examples", lambda db, key, page: [item] if has_sample else []
    )
    monkeypatch.setattr(showcase_service.repository, "archive_writable", lambda db: True)
    response = browser.get(f"/showcases/by-supplier/{KEY}")
    assert response.status_code == 200 and legacy == []
    assert UNSUPPORTED not in response.text and UNAVAILABLE not in response.text
    assert ("合成三聯式範例" if has_sample else "目前沒有範例") in response.text
    assert "加入範例發票</a>" in response.text


@pytest.mark.parametrize("sqlstate", ["42P01", "42501"])
def test_showcase_taiwan_database_failure_stays_503(
    showcase_page, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, sqlstate: str
) -> None:
    """台灣讀取的真實資料庫錯誤不能被轉成 200 或尚未支援。"""
    browser, _ = showcase_page
    master_as(monkeypatch, "TW")
    monkeypatch.setattr(showcase_service.v2, "supplier_examples", failing_read(sqlstate))
    caplog.set_level(logging.DEBUG)
    response = browser.get(f"/showcases/by-supplier/{KEY}")
    assert response.status_code == 503 and UNAVAILABLE in response.text
    assert UNSUPPORTED not in response.text
    assert_private_values_hidden(response, caplog)


@pytest.mark.parametrize("country", ["JP", "TW"])
def test_showcase_master_lookup_failure_is_not_unsupported(
    showcase_page, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, country: str
) -> None:
    """主檔查詢失敗時不知道國別，必須維持 503，不能當作尚未支援。"""
    browser, _ = showcase_page
    master_as(monkeypatch, country)

    def lookup(db, key):
        raise OperationalError(
            "private-sql", {"p": "private-param"}, SimpleNamespace(sqlstate="08006")
        )

    monkeypatch.setattr(repository, "get_supplier", lookup)
    calls: list[str] = []
    monkeypatch.setattr(showcase_service.repository, "search", failing_read("42P01", calls))
    monkeypatch.setattr(showcase_service.v2, "supplier_examples", failing_read("42P01", calls))
    caplog.set_level(logging.DEBUG)
    response = browser.get(f"/showcases/by-supplier/{KEY}")
    assert response.status_code == 503 and UNAVAILABLE in response.text
    assert UNSUPPORTED not in response.text and calls == []
    assert_private_values_hidden(response, caplog)


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
