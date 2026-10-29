"""正式帳號兩頁管理：合成資料、Session 替身，不連正式 DB。"""

from collections.abc import Iterator
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.db.repositories import user_account_repository as repository
from app.main import app
from app.routers.e_admin import require_configuration_access
from app.schemas.account import AccountSummary
from app.schemas.account_management import AccountDepartmentSettings
from app.services import account_management_service as service

ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
ACCOUNT = AccountSummary(user_id=ID, email="synthetic@example.invalid", account_status="active")
HEADERS = {"Origin": "http://invoiceguard-ai-admin.localhost:8080"}


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """合成授權前提下的業務回歸；保留 Host 與同源保護，不代表正式開放。"""
    monkeypatch.setattr(service, "list_accounts", lambda q="", page=1: ([ACCOUNT], 1))
    monkeypatch.setattr(service, "get_account", lambda user_id: ACCOUNT)
    monkeypatch.setattr(
        service,
        "get_department_settings",
        lambda user_id=None: AccountDepartmentSettings(
            departments=[], selected_ids=[], primary_department_id=None
        ),
    )
    # 僅隔離業務 UI；正式驗權與未接線拒絕行為由 test_e_login_access 驗證。
    monkeypatch.setitem(app.dependency_overrides, require_configuration_access, lambda: None)
    with TestClient(
        app,
        base_url="http://invoiceguard-ai-admin.localhost:8080",
        client=("127.0.0.1", 50000),
        follow_redirects=False,
    ) as browser:
        yield browser


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """攔截真實 DB Session，保留交易行為檢查。"""
    session = MagicMock()
    monkeypatch.setattr(repository, "open_session", lambda: session)
    return session


def test_list_is_separate_from_content(browser: TestClient) -> None:
    page = browser.get("/accounts")
    assert page.status_code == 200
    assert "/accounts/new" in page.text
    assert f"/accounts/{ID}/edit" in page.text
    assert 'type="password"' not in page.text
    assert "/dev/accounts" not in page.text
    assert "synthetic@example.invalid" in page.text


def test_new_and_edit_share_content_page(browser: TestClient) -> None:
    new = browser.get("/accounts/new")
    edit = browser.get(f"/accounts/{ID}/edit")
    assert new.status_code == edit.status_code == 200
    assert 'name="password"' in new.text
    assert 'name="new_password"' in edit.text
    assert 'name="current_password"' not in edit.text
    assert "data-confirm=" in edit.text
    assert "返回帳號列表" in new.text
    assert edit.headers["cache-control"] == "no-store"


def test_create_redirects_and_does_not_echo_password(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = MagicMock(return_value=ACCOUNT)
    monkeypatch.setattr(service, "create_account", create)
    response = browser.post(
        "/accounts/new",
        data={"email": ACCOUNT.email, "password": "synthetic-password"},
        headers=HEADERS,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/accounts/{ID}/edit?saved=created"
    assert "synthetic-password" not in response.text


def test_modify_does_not_require_old_password(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    update = MagicMock(return_value=ACCOUNT)
    monkeypatch.setattr(service, "update_account", update)
    response = browser.post(
        f"/accounts/{ID}/edit",
        data={"email": ACCOUNT.email, "new_password": "synthetic-reset"},
        headers=HEADERS,
    )
    assert response.status_code == 303
    update.assert_called_once_with(ID, ACCOUNT.email, "synthetic-reset")


def test_error_preserves_email_but_never_password(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = MagicMock(side_effect=service.AccountManagementError("此 Email 已被使用"))
    monkeypatch.setattr(service, "create_account", create)
    response = browser.post(
        "/accounts/new",
        data={"email": ACCOUNT.email, "password": "do-not-echo-this-password"},
        headers=HEADERS,
    )
    assert response.status_code == 400
    assert "此 Email 已被使用" in response.text
    assert ACCOUNT.email in response.text
    assert "do-not-echo-this-password" not in response.text


def test_disable_post_and_redirect(browser: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    disable = MagicMock(return_value=ACCOUNT)
    monkeypatch.setattr(service, "disable_account", disable)
    response = browser.post(f"/accounts/{ID}/disable", headers=HEADERS)
    assert response.status_code == 303
    assert response.headers["location"] == "/accounts?saved=disabled"
    disable.assert_called_once_with(ID)
    assert browser.get(f"/accounts/{ID}/disable").status_code == 405


@pytest.mark.parametrize(
    "origin", [None, "http://evil.example", "https://invoiceguard-ai-admin.localhost:8080"]
)
def test_write_origin_is_required(browser: TestClient, origin: str | None) -> None:
    headers = {"Origin": origin} if origin else {}
    assert browser.post(f"/accounts/{ID}/disable", headers=headers).status_code == 403


@pytest.mark.parametrize("environment", ["preview", "production"])
def test_cloud_routes_closed(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", environment)
    assert browser.get("/accounts").status_code == 404
    assert browser.post(f"/accounts/{ID}/disable", headers=HEADERS).status_code == 404


@pytest.mark.parametrize(
    "host,peer", [("evil.example", "127.0.0.1"), ("localhost", "192.168.1.22")]
)
def test_host_and_peer_guard(host: str, peer: str) -> None:
    browser = TestClient(app, base_url=f"http://{host}", client=(peer, 50000))
    assert browser.get("/accounts").status_code == 404


def test_get_missing_account_has_no_edit_form(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        service,
        "get_account",
        MagicMock(side_effect=service.AccountManagementError("找不到此帳號")),
    )
    response = browser.get(f"/accounts/{ID}/edit")
    assert response.status_code == 404
    assert 'name="new_password"' not in response.text


def test_service_create_reuses_existing_transaction(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = MagicMock(return_value=ACCOUNT)
    monkeypatch.setattr(service.user_service, "create_account", create)
    assert service.create_account(" SYNTHETIC@EXAMPLE.INVALID ", "synthetic-password") == ACCOUNT
    create.assert_called_once_with(db, email=ACCOUNT.email, password="synthetic-password")
    db.close.assert_called_once()


def test_direct_reset_hashes_password_and_commits(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    update = MagicMock(return_value=ACCOUNT)
    monkeypatch.setattr(repository, "update", update)
    monkeypatch.setattr(service, "hash_password", lambda password: "synthetic-hash")
    service.update_account(ID, ACCOUNT.email, "synthetic-new-password")
    update.assert_called_once_with(db, ID, email=ACCOUNT.email, password_hash="synthetic-hash")
    db.commit.assert_called_once()


def test_blank_reset_preserves_password(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    update = MagicMock(return_value=ACCOUNT)
    monkeypatch.setattr(repository, "update", update)
    hash_password = MagicMock()
    monkeypatch.setattr(service, "hash_password", hash_password)
    service.update_account(ID, ACCOUNT.email)
    update.assert_called_once_with(db, ID, email=ACCOUNT.email, password_hash=None)
    hash_password.assert_not_called()


def test_duplicate_update_rolls_back(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repository, "update", MagicMock(side_effect=repository.DuplicateEmailError())
    )
    with pytest.raises(service.AccountManagementError, match="已被使用"):
        service.update_account(ID, ACCOUNT.email)
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_disable_retains_data_and_commits(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository, "get", lambda *args: ACCOUNT)
    monkeypatch.setattr(repository, "supports_disabled_status", lambda db: True)
    disable = MagicMock(return_value=ACCOUNT.model_copy(update={"account_status": "disabled"}))
    monkeypatch.setattr(repository, "disable_account", disable)
    result = service.disable_account(ID)
    assert result.account_status == "disabled"
    disable.assert_called_once_with(db, ID)
    db.commit.assert_called_once()


def test_unverified_status_fails_closed(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository, "get", lambda *args: ACCOUNT)
    monkeypatch.setattr(repository, "supports_disabled_status", lambda db: False)
    disable = MagicMock()
    monkeypatch.setattr(repository, "disable_account", disable)
    with pytest.raises(service.AccountManagementError, match="狀態契約"):
        service.disable_account(ID)
    disable.assert_not_called()
    db.rollback.assert_called_once()


@pytest.mark.parametrize(
    "email", ["", "invalid", "x@@example.invalid", "x y@example.invalid", "x\x00@example.invalid"]
)
def test_invalid_email_before_db(email: str) -> None:
    with pytest.raises(service.AccountManagementError, match="Email"):
        service.create_account(email, "synthetic-password")


@pytest.mark.parametrize("password", ["", "x" * 257, "x\x00"])
def test_invalid_password_before_db(password: str) -> None:
    with pytest.raises(service.AccountManagementError, match="密碼"):
        service.create_account(ACCOUNT.email, password)


def test_database_error_is_sanitized(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository, "update", MagicMock(return_value=ACCOUNT))
    db.commit.side_effect = OperationalError("private SQL", {}, Exception("private host"))
    with pytest.raises(service.AccountManagementError) as error:
        service.update_account(ID, ACCOUNT.email)
    assert "private" not in str(error.value)
    db.rollback.assert_called_once()


def test_management_query_is_bound_and_paginated() -> None:
    db = MagicMock()
    count, rows = MagicMock(), MagicMock()
    count.scalar_one.return_value = 0
    rows.mappings.return_value = []
    db.execute.side_effect = [count, rows]
    query = "%' OR 1=1 --"
    assert repository.list_management(db, query=query, limit=20, offset=40) == ([], 0)
    statement, params = db.execute.call_args.args
    assert query not in str(statement)
    assert params == {"q": query, "limit": 20, "offset": 40}


def test_disable_sql_updates_status_only() -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value.one_or_none.return_value = ACCOUNT.model_dump()
    repository.disable_account(db, ID)
    statement, params = db.execute.call_args.args
    assert "DELETE" not in str(statement)
    assert "password_hash" not in str(statement)
    assert params == {"user_id": ID, "status": "disabled"}


@pytest.mark.parametrize(
    "definition,expected",
    [
        ("CHECK (account_status = ANY (ARRAY['active', 'disabled']))", True),
        ("CHECK (account_status IN ('active', 'suspended'))", False),
    ],
)
def test_real_status_contract_is_checked(definition: str, expected: bool) -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value = [{"definition": definition}]
    assert repository.supports_disabled_status(db) is expected
