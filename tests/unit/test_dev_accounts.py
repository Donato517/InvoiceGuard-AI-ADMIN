"""模組名稱：tests.unit.test_dev_accounts；功能說明：開發者帳號（查詢／新增／修改）的分層、雲端 API 與本機 DB 模組直連測試；全部使用合成資料與替身，不連 DB／網路。"""

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from app.core import deps
from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.db import session as db_session
from app.db.repositories import user_account_repository as repository
from app.db.session import get_db
from app.integrations import remote_admin_client
from app.main import app
from app.schemas.account import AccountSummary
from app.services import dev_account_service, user_service

REMOTE = "https://admin.example.com"
PASSWORD = "synthetic-password-never-echoed"
NEW_PASSWORD = "synthetic-new-password-never-echoed"
DEPARTMENT = uuid4()


def make_settings(**values: Any) -> Settings:
    """以隔離前綴與合成值建構，不讀取公司環境變數。"""
    return Settings(_env_file=None, _env_prefix="IG_UNIT_TEST_", **values)


CLOUD = make_settings(VERCEL="1", VERCEL_ENV="production", APP_ENV="production")
LOCAL = make_settings(REMOTE_ADMIN_BASE_URL=REMOTE)


class Store:
    """以記憶體模擬帳號相關資料表，取代 repository；記錄每次使用的 DB Session。"""

    def __init__(self) -> None:
        self.accounts: dict[UUID, dict[str, Any]] = {}
        self.profiles: dict[UUID, str] = {}
        self.memberships: list[tuple[UUID, UUID]] = []
        self.sessions: list[Any] = []

    def _summary(self, user_id: UUID) -> AccountSummary:
        row = self.accounts[user_id]
        return AccountSummary(
            user_id=user_id, email=row["email"], account_status=row["account_status"]
        )

    def list_recent(self, db: Any, *, limit: int) -> list[AccountSummary]:
        self.sessions.append(db)
        return [self._summary(user_id) for user_id in reversed(self.accounts)][:limit]

    def get(self, db: Any, user_id: UUID) -> AccountSummary | None:
        self.sessions.append(db)
        return self._summary(user_id) if user_id in self.accounts else None

    def find_by_email(self, db: Any, email: str) -> AccountSummary | None:
        self.sessions.append(db)
        found = [key for key, row in self.accounts.items() if row["email"] == email]
        return self._summary(found[0]) if found else None

    def get_password_hash(self, db: Any, user_id: UUID) -> str | None:
        self.sessions.append(db)
        row = self.accounts.get(user_id)
        return row["password_hash"] if row else None

    def ensure_default_department(self, db: Any) -> UUID:
        self.sessions.append(db)
        return DEPARTMENT

    def create_active(
        self, db: Any, *, email: str, password_hash: str, display_name: str, department_id: UUID
    ) -> AccountSummary:
        self.sessions.append(db)
        if any(row["email"] == email for row in self.accounts.values()):
            raise repository.DuplicateEmailError
        user_id = uuid4()
        self.accounts[user_id] = {
            "email": email,
            "password_hash": password_hash,
            "account_status": "active",
            "primary_department_id": department_id,
        }
        self.profiles[user_id] = display_name
        self.memberships.append((user_id, department_id))
        return self._summary(user_id)

    def update(
        self, db: Any, user_id: UUID, *, email: str, password_hash: str | None
    ) -> AccountSummary | None:
        self.sessions.append(db)
        if user_id not in self.accounts:
            return None
        if any(row["email"] == email and key != user_id for key, row in self.accounts.items()):
            raise repository.DuplicateEmailError
        self.accounts[user_id]["email"] = email
        if password_hash is not None:
            self.accounts[user_id]["password_hash"] = password_hash
        return self._summary(user_id)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    """以記憶體 Store 取代 repository 的資料存取函式。"""
    fake = Store()
    for name in (
        "list_recent",
        "get",
        "find_by_email",
        "get_password_hash",
        "ensure_default_department",
        "create_active",
        "update",
    ):
        monkeypatch.setattr(repository, name, getattr(fake, name))
    return fake


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """雲端模式：注入替身 DB Session（固定系統帳號的既有 DB 模組）。"""
    session = MagicMock(name="injected-db-session")
    monkeypatch.setattr(deps, "settings", CLOUD)
    app.dependency_overrides[get_db] = lambda: session
    yield session
    app.dependency_overrides.clear()


@pytest.fixture
def local(monkeypatch: pytest.MonkeyPatch) -> None:
    """本機 remote 模式（開發示範頁開放）。"""
    monkeypatch.setattr(deps, "settings", LOCAL)


def summary(email: str = "a@example.com") -> AccountSummary:
    """合成帳號。"""
    return AccountSummary(user_id=uuid4(), email=email, account_status="active")


# ---- repository ----


@pytest.mark.parametrize(
    "statement",
    [
        repository.LIST_RECENT,
        repository.SELECT_BY_ID,
        repository.SELECT_BY_EMAIL,
        repository.INSERT_ACCOUNT,
        repository.UPDATE_ACCOUNT,
    ],
)
def test_returned_columns_never_include_password_hash(statement: Any) -> None:
    sql = str(statement)
    returned = sql.split("RETURNING")[-1] if "RETURNING" in sql else sql
    assert "password_hash" not in returned
    assert "public.users" not in sql


def test_create_active_writes_account_profile_and_membership_in_order() -> None:
    db = MagicMock()
    user_id = uuid4()
    row = {"user_id": user_id, "email": "a@example.com", "account_status": "active"}
    db.execute.return_value.mappings.return_value.one.return_value = row
    account = repository.create_active(
        db,
        email="a@example.com",
        password_hash="$argon2id$x",
        display_name="a",
        department_id=DEPARTMENT,
    )
    assert account == AccountSummary(**row)
    statements = [item.args[0] for item in db.execute.call_args_list]
    assert statements == [
        repository.INSERT_ACCOUNT,
        repository.INSERT_PROFILE,
        repository.INSERT_MEMBERSHIP,
    ]
    assert "'active'" in str(repository.INSERT_ACCOUNT)
    assert db.execute.call_args_list[1].args[1] == {"user_id": user_id, "display_name": "a"}
    assert db.execute.call_args_list[2].args[1] == {"user_id": user_id, "department_id": DEPARTMENT}
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_default_department_is_created_once_then_reused() -> None:
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = DEPARTMENT
    assert repository.ensure_default_department(db) == DEPARTMENT
    assert db.execute.call_args_list[0] == call(
        repository.ENSURE_DEFAULT_DEPARTMENT,
        {"code": repository.DEFAULT_DEPARTMENT_CODE, "name": repository.DEFAULT_DEPARTMENT_NAME},
    )
    assert "ON CONFLICT (department_code) DO NOTHING" in str(repository.ENSURE_DEFAULT_DEPARTMENT)


def test_inactive_default_department_is_reported() -> None:
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = None
    with pytest.raises(repository.AccountStoreError) as caught:
        repository.ensure_default_department(db)
    assert caught.value.code == "default_department_unavailable"


def db_error(kind: type[Exception], **diag: Any) -> Exception:
    """帶約束名稱或 SQLSTATE 的合成驅動錯誤。"""
    original = Exception("synthetic")
    original.diag = SimpleNamespace(constraint_name=diag.get("constraint"))  # type: ignore[attr-defined]
    original.sqlstate = diag.get("state")  # type: ignore[attr-defined]
    return kind("SQL", None, original)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "error,expected",
    [
        (db_error(IntegrityError, constraint="ux_user_accounts_email"), "duplicate"),
        (db_error(IntegrityError, constraint="user_accounts_email_basic_shape"), "raw"),
        (db_error(ProgrammingError, state="42P01"), "account_table_missing"),
        (db_error(ProgrammingError, state="42703"), "account_schema_mismatch"),
        (db_error(ProgrammingError, state="42501"), "account_permission_denied"),
        (db_error(ProgrammingError, state="42601"), "raw"),
    ],
)
def test_repository_translates_only_known_db_errors(error: Exception, expected: str) -> None:
    db = MagicMock()
    db.execute.side_effect = error
    with pytest.raises(Exception) as caught:
        repository.update(db, uuid4(), email="a@example.com", password_hash=None)
    if expected == "duplicate":
        assert isinstance(caught.value, repository.DuplicateEmailError)
    elif expected == "raw":
        assert caught.value is error
    else:
        assert isinstance(caught.value, repository.AccountStoreError)
        assert caught.value.code == expected


# ---- service（雲端，DB）----


def test_create_makes_usable_active_account_in_one_transaction(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    db = MagicMock()
    with caplog.at_level(logging.INFO):
        account = user_service.create_account(db, email=" Dev.One@Example.COM ", password=PASSWORD)
    assert (account.email, account.account_status) == ("dev.one@example.com", "active")
    stored = store.accounts[account.user_id]
    assert stored["primary_department_id"] == DEPARTMENT
    assert (
        stored["password_hash"].startswith("$argon2id$") and PASSWORD not in stored["password_hash"]
    )
    assert store.profiles[account.user_id] == "dev.one"
    assert store.memberships == [(account.user_id, DEPARTMENT)]
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
    assert f"account_created operator={account.user_id}" in caplog.text
    assert PASSWORD not in caplog.text


def test_search_by_blank_uuid_and_email(store: Store) -> None:
    db = MagicMock()
    first = user_service.create_account(db, email="first@example.com", password=PASSWORD)
    user_service.create_account(db, email="second@example.com", password=PASSWORD)
    assert [a.email for a in user_service.search_accounts(db)] == [
        "second@example.com",
        "first@example.com",
    ]
    assert user_service.search_accounts(db, str(first.user_id)) == [first]
    assert user_service.search_accounts(db, "  FIRST@example.com ") == [first]
    assert user_service.search_accounts(db, "nobody@example.com") == []


@pytest.mark.parametrize(
    "email,password,code",
    [
        ("no-at-sign", PASSWORD, "invalid_email"),
        ("@x", PASSWORD, "invalid_email"),
        ("a@b", "", "password_required"),
    ],
)
def test_create_rejects_bad_input_before_db(
    store: Store, email: str, password: str, code: str
) -> None:
    db = MagicMock()
    with pytest.raises(user_service.AccountInputError) as caught:
        user_service.create_account(db, email=email, password=password)
    assert caught.value.code == code
    assert store.sessions == []
    db.commit.assert_not_called()


def test_duplicate_email_rolls_back(store: Store) -> None:
    db = MagicMock()
    user_service.create_account(db, email="a@example.com", password=PASSWORD)
    db.reset_mock()
    with pytest.raises(user_service.AccountInputError) as caught:
        user_service.create_account(db, email="A@example.com", password=PASSWORD)
    assert caught.value.code == "email_taken"
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


@pytest.mark.parametrize("failure", ["department", "insert", "commit"])
def test_any_db_failure_rolls_back_whole_creation(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    db = MagicMock()
    boom = MagicMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(
        repository,
        "ensure_default_department",
        boom if failure == "department" else MagicMock(return_value=DEPARTMENT),
    )
    monkeypatch.setattr(
        repository,
        "create_active",
        boom if failure == "insert" else MagicMock(return_value=summary()),
    )
    if failure == "commit":
        db.commit.side_effect = RuntimeError("deferred foreign key failed")
    with pytest.raises(RuntimeError):
        user_service.create_account(db, email="a@example.com", password=PASSWORD)
    db.rollback.assert_called_once()


@pytest.mark.parametrize(
    "new_password,rehashed", [(None, False), ("", False), (NEW_PASSWORD, True)]
)
def test_update_with_current_password(
    store: Store, caplog: pytest.LogCaptureFixture, new_password: str | None, rehashed: bool
) -> None:
    db = MagicMock()
    account = user_service.create_account(db, email="a@example.com", password=PASSWORD)
    before = store.accounts[account.user_id]["password_hash"]
    with caplog.at_level(logging.INFO):
        updated = user_service.update_account(
            db,
            account.user_id,
            email="B@example.com",
            current_password=PASSWORD,
            new_password=new_password,
        )
    assert updated.email == "b@example.com"
    assert (store.accounts[account.user_id]["password_hash"] != before) is rehashed
    assert user_service.search_accounts(db, str(account.user_id))[0].email == "b@example.com"
    assert f"account_updated operator={account.user_id} target={account.user_id}" in caplog.text
    assert PASSWORD not in caplog.text and NEW_PASSWORD not in caplog.text


@pytest.mark.parametrize("current", ["", "wrong-password"])
def test_update_rejects_wrong_current_password(store: Store, current: str) -> None:
    db = MagicMock()
    account = user_service.create_account(db, email="a@example.com", password=PASSWORD)
    db.reset_mock()
    with pytest.raises(user_service.AccountInputError) as caught:
        user_service.update_account(
            db,
            account.user_id,
            email="b@example.com",
            current_password=current,
            new_password=NEW_PASSWORD,
        )
    assert caught.value.code == "current_password_invalid"
    assert store.accounts[account.user_id]["email"] == "a@example.com"
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_update_unknown_account_rolls_back(store: Store) -> None:
    db = MagicMock()
    with pytest.raises(NotFoundError):
        user_service.update_account(
            db, uuid4(), email="a@example.com", current_password=PASSWORD, new_password=None
        )
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


# ---- 雲端 API ----


def test_api_is_404_on_local_without_db_attempt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, store: Store
) -> None:
    monkeypatch.setattr(deps, "settings", LOCAL)
    factory = MagicMock()
    app.dependency_overrides[get_db] = factory
    try:
        assert client.get("/api/dev/accounts").status_code == 404
        assert client.post("/api/dev/accounts", json={}).status_code == 404
    finally:
        app.dependency_overrides.clear()
    factory.assert_not_called()


def test_api_insert_select_update_use_injected_session(
    client: TestClient, cloud: MagicMock, store: Store
) -> None:
    created = client.post(
        "/api/dev/accounts", json={"email": "A@example.com", "password": PASSWORD}
    )
    assert created.status_code == 201
    account = created.json()["account"]
    assert (account["email"], account["account_status"]) == ("a@example.com", "active")
    assert client.get("/api/dev/accounts", params={"q": "a@example.com"}).json()["items"] == [
        account
    ]

    patched = client.patch(
        f"/api/dev/accounts/{account['user_id']}",
        json={"email": "b@example.com", "current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )
    assert patched.status_code == 200
    again = client.get("/api/dev/accounts", params={"q": account["user_id"]}).json()["items"]
    assert again[0]["email"] == "b@example.com"
    assert store.sessions and all(session is cloud for session in store.sessions)
    texts = created.text + patched.text
    assert PASSWORD not in texts and NEW_PASSWORD not in texts and "password_hash" not in texts


@pytest.mark.usefixtures("cloud")
def test_api_reports_fixed_error_codes(
    client: TestClient, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"email": "a@example.com", "password": PASSWORD}
    account = client.post("/api/dev/accounts", json=body).json()["account"]
    duplicate = client.post("/api/dev/accounts", json=body)
    assert (duplicate.status_code, duplicate.json()) == (409, {"error": {"code": "email_taken"}})
    invalid = client.post("/api/dev/accounts", json={"email": "bad", "password": PASSWORD})
    assert (invalid.status_code, invalid.json()) == (400, {"error": {"code": "invalid_email"}})
    wrong = client.patch(
        f"/api/dev/accounts/{account['user_id']}",
        json={"email": "c@example.com", "current_password": "wrong"},
    )
    assert (wrong.status_code, wrong.json()) == (
        400,
        {"error": {"code": "current_password_invalid"}},
    )
    missing = client.patch(
        f"/api/dev/accounts/{uuid4()}",
        json={"email": "c@example.com", "current_password": PASSWORD},
    )
    assert (missing.status_code, missing.json()) == (404, {"error": {"code": "not_found"}})
    error = repository.AccountStoreError("account_table_missing")
    monkeypatch.setattr(repository, "list_recent", MagicMock(side_effect=error))
    table = client.get("/api/dev/accounts")
    assert (table.status_code, table.json()) == (503, {"error": {"code": "account_table_missing"}})


@pytest.mark.usefixtures("cloud")
def test_api_rejects_cross_site_browser_write(client: TestClient, store: Store) -> None:
    response = client.post(
        "/api/dev/accounts",
        json={"email": "a@example.com", "password": PASSWORD},
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert store.accounts == {}


# ---- 本機頁面 ----


def test_page_is_404_in_cloud(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "settings", CLOUD)
    assert client.get("/dev/accounts").status_code == 404


@pytest.mark.usefixtures("local")
def test_page_lists_accounts_from_cloud(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = summary()
    search = MagicMock(return_value=[account])
    monkeypatch.setattr(dev_account_service, "search", search)
    response = client.get("/dev/accounts", params={"q": "a@example.com"})
    assert response.status_code == 200
    assert str(account.user_id) in response.text and "active" in response.text
    search.assert_called_once_with("a@example.com")


@pytest.mark.usefixtures("local")
def test_page_create_and_update_respond_in_place_without_redirect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = summary()
    create = MagicMock(return_value=account)
    update = MagicMock(return_value=account)
    search = MagicMock(return_value=[account])
    for name, mock in (("create", create), ("update", update), ("search", search)):
        monkeypatch.setattr(dev_account_service, name, mock)

    created = client.post("/dev/accounts", data={"email": "a@example.com", "password": PASSWORD})
    assert created.status_code == 200 and "location" not in created.headers
    create.assert_called_once_with(email="a@example.com", password=PASSWORD)
    assert "已新增帳號" in created.text
    assert f'hx-post="/dev/accounts/{account.user_id}"' in created.text
    assert 'name="current_password"' in created.text
    assert PASSWORD not in created.text

    updated = client.post(
        f"/dev/accounts/{account.user_id}",
        data={"email": "b@example.com", "current_password": PASSWORD},
    )
    assert updated.status_code == 200 and "location" not in updated.headers
    assert "已更新帳號" in updated.text
    update.assert_called_once_with(
        account.user_id, email="b@example.com", current_password=PASSWORD, new_password=""
    )
    assert PASSWORD not in updated.text


@pytest.mark.usefixtures("local")
def test_page_submits_in_place_and_ignores_messages_in_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dev_account_service, "search", MagicMock(return_value=[summary()]))
    page = client.get("/dev/accounts", params={"saved": "created"})
    assert page.status_code == 200
    assert "已新增帳號" not in page.text and "已更新帳號" not in page.text
    assert 'hx-target="#dev-accounts"' in page.text and 'hx-select="#dev-accounts"' in page.text
    assert 'hx-get="/dev/accounts"' in page.text and 'hx-post="/dev/accounts"' in page.text
    # 400（輸入錯誤）也要替換到畫面；設定寫在頁面而非靜態 JS，避免瀏覽器沿用快取的舊檔
    assert 'name="htmx-config"' in page.text and '{"code":"400","swap":true}' in page.text


@pytest.mark.usefixtures("local")
def test_page_errors_refill_email_but_never_password(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = dev_account_service.DevAccountError("email_taken")
    monkeypatch.setattr(dev_account_service, "create", MagicMock(side_effect=error))
    monkeypatch.setattr(dev_account_service, "search", MagicMock(return_value=[]))
    response = client.post("/dev/accounts", data={"email": "a@example.com", "password": PASSWORD})
    assert response.status_code == 400
    assert "此 Email 已被使用" in response.text
    assert 'value="a@example.com"' in response.text
    assert PASSWORD not in response.text


@pytest.mark.usefixtures("local")
def test_page_rejects_cross_site_post(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    create = MagicMock()
    monkeypatch.setattr(dev_account_service, "create", create)
    response = client.post(
        "/dev/accounts",
        data={"email": "a@example.com", "password": PASSWORD},
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    create.assert_not_called()


# ---- 本機 service（既有 DB 模組，DATABASE_URL 由 START 注入）----


@pytest.fixture
def local_db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """本機程序已有 DATABASE_URL：open_session 改回傳替身 Session，不連 DB。"""
    session = MagicMock(name="local-db-session")
    monkeypatch.setattr(repository, "open_session", MagicMock(return_value=session))
    return session


def test_local_service_select_insert_update_use_db_module_session(
    local_db: MagicMock, store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert dev_account_service.search() == []
        created = dev_account_service.create(email="A@example.com", password=PASSWORD)
        assert created.account_status == "active"
        assert dev_account_service.search("a@example.com") == [created]

        updated = dev_account_service.update(
            created.user_id,
            email="b@example.com",
            current_password=PASSWORD,
            new_password=NEW_PASSWORD,
        )
        assert updated.email == "b@example.com"
        assert dev_account_service.search(str(created.user_id))[0].email == "b@example.com"

        with pytest.raises(dev_account_service.DevAccountError) as wrong:
            dev_account_service.update(
                created.user_id, email="c@example.com", current_password=PASSWORD, new_password=""
            )
        assert wrong.value.code == "current_password_invalid"

        with pytest.raises(dev_account_service.DevAccountError) as duplicate:
            dev_account_service.create(email="b@example.com", password=PASSWORD)
        assert duplicate.value.code == "email_taken"
    assert store.accounts[created.user_id]["password_hash"].startswith("$argon2id$")
    assert store.sessions and all(session is local_db for session in store.sessions)
    assert local_db.commit.call_count == 2
    assert local_db.close.call_count == 7
    assert PASSWORD not in caplog.text and NEW_PASSWORD not in caplog.text


def test_local_update_blank_new_password_keeps_password(local_db: MagicMock, store: Store) -> None:
    created = dev_account_service.create(email="a@example.com", password=PASSWORD)
    original = store.accounts[created.user_id]["password_hash"]
    dev_account_service.update(
        created.user_id, email="a@example.com", current_password=PASSWORD, new_password=""
    )
    assert store.accounts[created.user_id]["password_hash"] == original


@pytest.mark.parametrize(
    "error,code",
    [
        (user_service.AccountInputError("email_taken", "x"), "email_taken"),
        (repository.AccountStoreError("account_table_missing"), "account_table_missing"),
        (NotFoundError(), "not_found"),
        (OperationalError("SELECT 1", {}, Exception("synthetic")), "db_unavailable"),
    ],
)
def test_local_service_maps_errors_to_fixed_messages_and_closes(
    local_db: MagicMock, monkeypatch: pytest.MonkeyPatch, error: Exception, code: str
) -> None:
    monkeypatch.setattr(user_service, "create_account", MagicMock(side_effect=error))
    with pytest.raises(dev_account_service.DevAccountError) as caught:
        dev_account_service.create(email="a@example.com", password=PASSWORD)
    assert caught.value.code == code
    assert str(caught.value) == dev_account_service.MESSAGES[code]
    local_db.close.assert_called_once()


def test_local_service_without_database_url_never_calls_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_session, "settings", make_settings())
    request = MagicMock()
    monkeypatch.setattr(remote_admin_client, "request_json", request)
    with pytest.raises(dev_account_service.DevAccountError) as caught:
        dev_account_service.search()
    assert caught.value.code == "database_not_configured"
    request.assert_not_called()


def test_open_session_binds_existing_db_module_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = make_settings(
        DATABASE_URL="postgresql://synthetic:synthetic@db.example.com:6543/postgres?sslmode=require"
    )
    monkeypatch.setattr(db_session, "settings", configured)
    db_session._get_engine.cache_clear()
    try:
        session = repository.open_session()
        assert session.get_bind().url.host == "db.example.com"
        session.close()
    finally:
        db_session._get_engine.cache_clear()


# ---- 本機預設雲端網址（remote_admin_client 共用傳輸仍可使用）----


@pytest.mark.parametrize(
    "configuration,expected",
    [
        (make_settings(REMOTE_ADMIN_BASE_URL=""), "https://invoice-guard-ai-admin.vercel.app"),
        (make_settings(REMOTE_ADMIN_BASE_URL=REMOTE), REMOTE),
        (CLOUD, ""),
    ],
)
def test_local_remote_mode_needs_no_manual_url(configuration: Settings, expected: str) -> None:
    assert configuration.remote_admin_base_url == expected
