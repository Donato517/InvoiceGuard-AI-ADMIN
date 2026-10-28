"""帳號多部門設定：驗證主要部門、交易順序、權限關聯保護與表單，不連正式 DB。"""

from collections.abc import Iterator
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.repositories import user_account_repository as repository
from app.main import app
from app.routers.e_admin import require_configuration_access
from app.schemas.account import AccountSummary
from app.schemas.account_management import AccountDepartmentSettings
from app.schemas.department import Department
from app.services import account_management_service as service

USER = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
FIRST = UUID("11111111-1111-4111-8111-111111111111")
SECOND = UUID("22222222-2222-4222-8222-222222222222")
ACCOUNT = AccountSummary(user_id=USER, email="synthetic@example.invalid", account_status="active")
OPTIONS = [
    Department(
        department_id=key,
        department_code=code,
        department_name="虛構部門",
        is_active=True,
        created_at=None,
        updated_at=None,
    )
    for key, code in [(FIRST, "UNASSIGNED"), (SECOND, "TEST_ONLY")]
]
HEADERS = {"Origin": "http://invoiceguard-ai-admin.localhost:8080"}


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """合成兩部門及一帳號，保留交易行為斷言。"""
    session = MagicMock()
    monkeypatch.setattr(repository, "open_session", lambda: session)
    monkeypatch.setattr(repository, "get", lambda *args: ACCOUNT)
    monkeypatch.setattr(repository, "lock_department_settings", lambda *args: FIRST)
    monkeypatch.setattr(repository, "get_department_ids", lambda *args: (FIRST, [FIRST]))
    monkeypatch.setattr(
        repository, "lock_selected_departments", lambda db, ids: {key: True for key in ids}
    )
    monkeypatch.setattr(repository, "membership_removal_blocker", lambda db: None)
    return session


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """合成授權前提下的業務回歸；保留 Host 與同源保護，不代表正式開放。"""
    monkeypatch.setattr(service, "get_account", lambda user: ACCOUNT)
    monkeypatch.setattr(
        service,
        "get_department_settings",
        lambda user=None: AccountDepartmentSettings(
            departments=OPTIONS, selected_ids=[FIRST, SECOND], primary_department_id=FIRST
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


def test_new_and_edit_offer_multiple_departments(browser: TestClient) -> None:
    for path in ["/accounts/new", f"/accounts/{USER}/edit"]:
        response = browser.get(path)
        assert response.status_code == 200
        assert response.text.count('name="department_ids"') == 2
        assert 'name="primary_department_id"' in response.text
    assert f'action="/accounts/{USER}/departments"' in browser.get(f"/accounts/{USER}/edit").text


def test_department_post_accepts_multiple_values(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    save = MagicMock()
    monkeypatch.setattr(service, "save_department_settings", save)
    response = browser.post(
        f"/accounts/{USER}/departments",
        data={"department_ids": [str(FIRST), str(SECOND)], "primary_department_id": str(SECOND)},
        headers=HEADERS,
    )
    assert response.status_code == 303
    save.assert_called_once_with(USER, [FIRST, SECOND], SECOND)
    assert response.headers["location"].endswith("saved=departments")


def test_department_post_preserves_input_on_failure(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        service,
        "save_department_settings",
        MagicMock(side_effect=service.AccountManagementError("主要部門必須是已勾選的所屬部門")),
    )
    response = browser.post(
        f"/accounts/{USER}/departments",
        data={"department_ids": [str(SECOND)], "primary_department_id": str(FIRST)},
        headers=HEADERS,
    )
    assert response.status_code == 400
    assert "主要部門必須是已勾選" in response.text
    assert f'value="{SECOND}"' in response.text
    assert 'name="new_password"' in response.text


def test_department_post_rejects_missing_origin(browser: TestClient) -> None:
    assert browser.post(f"/accounts/{USER}/departments").status_code == 403


def test_create_posts_department_selection(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = MagicMock(return_value=ACCOUNT)
    monkeypatch.setattr(service, "create_account", create)
    response = browser.post(
        "/accounts/new",
        data={
            "email": ACCOUNT.email,
            "password": "synthetic",
            "department_ids": [str(FIRST), str(SECOND)],
            "primary_department_id": str(SECOND),
        },
        headers=HEADERS,
    )
    assert response.status_code == 303
    create.assert_called_once_with(
        ACCOUNT.email, "synthetic", department_ids=[FIRST, SECOND], primary_department_id=SECOND
    )


@pytest.mark.parametrize("selected,primary", [([], None), ([FIRST], None), ([FIRST], SECOND)])
def test_invalid_selection_before_database(selected: list[UUID], primary: UUID | None) -> None:
    with pytest.raises(service.AccountManagementError):
        service.save_department_settings(USER, selected, primary)


def test_create_all_memberships_one_transaction(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    create, add = MagicMock(return_value=ACCOUNT), MagicMock()
    monkeypatch.setattr(repository, "create_active", create)
    monkeypatch.setattr(repository, "add_membership", add)
    monkeypatch.setattr(service, "hash_password", lambda value: "synthetic-hash")
    service.create_account(
        ACCOUNT.email,
        "synthetic",
        department_ids=[FIRST, SECOND, SECOND],
        primary_department_id=SECOND,
    )
    assert create.call_args.kwargs["department_id"] == SECOND
    add.assert_called_once_with(db, USER, FIRST)
    db.commit.assert_called_once()
    db.close.assert_called_once()


def test_save_adds_before_primary_then_removes(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(repository, "add_membership", lambda *args: calls.append("add"))
    monkeypatch.setattr(repository, "set_primary_department", lambda *args: calls.append("primary"))
    monkeypatch.setattr(repository, "remove_membership", lambda *args: calls.append("remove"))
    db.commit.side_effect = lambda: calls.append("commit")
    service.save_department_settings(USER, [SECOND], SECOND)
    assert calls == ["add", "primary", "remove", "commit"]


def test_retained_membership_not_reinserted(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    add, remove = MagicMock(), MagicMock()
    monkeypatch.setattr(repository, "add_membership", add)
    monkeypatch.setattr(repository, "remove_membership", remove)
    monkeypatch.setattr(repository, "set_primary_department", MagicMock())
    service.save_department_settings(USER, [FIRST], FIRST)
    add.assert_not_called()
    remove.assert_not_called()
    db.commit.assert_called_once()


def test_new_inactive_department_rejected(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repository, "lock_selected_departments", lambda *args: {FIRST: True, SECOND: False}
    )
    with pytest.raises(service.AccountManagementError, match="停用"):
        service.save_department_settings(USER, [FIRST, SECOND], FIRST)
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_existing_inactive_membership_can_be_retained(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repository, "get_department_ids", lambda *args: (FIRST, [FIRST, SECOND]))
    monkeypatch.setattr(
        repository, "lock_selected_departments", lambda *args: {FIRST: True, SECOND: False}
    )
    setter = MagicMock()
    monkeypatch.setattr(repository, "set_primary_department", setter)
    service.save_department_settings(USER, [FIRST, SECOND], FIRST)
    setter.assert_called_once_with(db, USER, FIRST)


def test_inactive_cannot_become_new_primary(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository, "get_department_ids", lambda *args: (FIRST, [FIRST, SECOND]))
    monkeypatch.setattr(
        repository, "lock_selected_departments", lambda *args: {FIRST: True, SECOND: False}
    )
    with pytest.raises(service.AccountManagementError, match="新的主要部門"):
        service.save_department_settings(USER, [FIRST, SECOND], SECOND)


def test_missing_department_rejected(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository, "lock_selected_departments", lambda *args: {FIRST: True})
    with pytest.raises(service.AccountManagementError, match="不存在"):
        service.save_department_settings(USER, [FIRST, SECOND], FIRST)


def test_unknown_foreign_key_rejects_removal_before_any_write(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        repository, "membership_removal_blocker", lambda db: "部門關聯有尚未核對的外鍵"
    )
    add = MagicMock()
    monkeypatch.setattr(repository, "add_membership", add)
    with pytest.raises(service.AccountManagementError, match="外鍵"):
        service.save_department_settings(USER, [SECOND], SECOND)
    add.assert_not_called()
    db.commit.assert_not_called()


def test_failure_rolls_back_all_membership_changes(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repository, "add_membership", MagicMock())
    monkeypatch.setattr(
        repository,
        "set_primary_department",
        MagicMock(side_effect=OperationalError("private SQL", {}, Exception("private host"))),
    )
    with pytest.raises(service.AccountManagementError):
        service.save_department_settings(USER, [FIRST, SECOND], SECOND)
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
    db.close.assert_called_once()


def test_create_membership_failure_never_commits_account(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "hash_password", lambda value: "synthetic-hash")
    monkeypatch.setattr(repository, "create_active", MagicMock(return_value=ACCOUNT))
    monkeypatch.setattr(
        repository,
        "add_membership",
        MagicMock(side_effect=OperationalError("SQL", {}, Exception())),
    )
    with pytest.raises(service.AccountManagementError):
        service.create_account(
            ACCOUNT.email, "synthetic", department_ids=[FIRST, SECOND], primary_department_id=FIRST
        )
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_get_new_department_defaults(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service.department_repository, "search", lambda *args: OPTIONS)
    result = service.get_department_settings()
    assert result.primary_department_id == FIRST
    assert result.selected_ids == [FIRST]
    db.commit.assert_not_called()


def test_remove_is_scoped_to_one_account_and_department() -> None:
    db = MagicMock()
    repository.remove_membership(db, USER, FIRST)
    statement, params = db.execute.call_args.args
    assert "WHERE user_id = :user_id AND department_id = :department_id" in str(statement)
    assert params == {"user_id": USER, "department_id": FIRST}
    assert "SET unassigned_at = now()" in str(statement)
    assert "SET is_current" not in str(statement)
    assert "DELETE" not in str(statement)


def test_existing_membership_reactivated_without_overwriting_other_fields() -> None:
    db = MagicMock()
    repository.add_membership(db, USER, FIRST)
    statement, params = db.execute.call_args.args
    assert "ON CONFLICT (user_id, department_id) DO UPDATE SET unassigned_at = NULL" in str(
        statement
    )
    assert "SET is_current" not in str(statement)
    assert params == {"user_id": USER, "department_id": FIRST}


def test_actual_three_column_primary_membership_fk_supported() -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value = [
        {
            "schema_name": "invoiceguard_identity",
            "table_name": "user_accounts",
            "source_columns": ["user_id", "primary_department_id", "primary_department_active"],
            "target_columns": ["user_id", "department_id", "is_current"],
        }
    ]
    assert repository.membership_removal_blocker(db) is None


@pytest.mark.parametrize(
    "table,supported", [("user_accounts", True), ("department_permissions", False)]
)
def test_membership_foreign_key_contract(table: str, supported: bool) -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value = [
        {
            "schema_name": "invoiceguard_identity",
            "table_name": table,
            "source_columns": ["user_id", "primary_department_id"],
            "target_columns": ["user_id", "department_id"],
        }
    ]
    assert (repository.membership_removal_blocker(db) is None) is supported
