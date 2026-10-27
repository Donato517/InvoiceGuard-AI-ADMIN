"""部門管理回歸測試；僅使用合成資料及替身，不連正式資料庫。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import settings
from app.db.repositories import department_repository as repository
from app.main import app
from app.schemas.department import Department
from app.services import department_service as service

ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ROW = Department(
    department_id=ID,
    department_code="TEST_ONLY",
    department_name="虛構測試部門",
    is_active=True,
    created_at=datetime.now(UTC),
    updated_at=datetime.now(UTC),
)


@pytest.fixture
def local_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """模擬真正 loopback 連線，不略過路由保護。"""
    monkeypatch.setattr(service, "search", lambda q="": [ROW])
    return TestClient(
        app, base_url="http://invoiceguard-ai-admin.localhost:8080", client=("127.0.0.1", 50000)
    )


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """替換 Session，檢查 commit、rollback 與 close。"""
    session = MagicMock()
    monkeypatch.setattr(repository, "open_session", lambda: session)
    return session


def test_page_and_xss(local_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "search",
        lambda q="": [ROW.model_copy(update={"department_name": "<script>alert(1)</script>"})],
    )
    response = local_client.get("/department")
    assert response.status_code == 200
    assert "&lt;script&gt;" in response.text
    assert "data-confirm=" in response.text
    assert "部門管理" in response.text


@pytest.mark.parametrize("environment", ["preview", "production"])
def test_cloud_is_closed(
    local_client: TestClient, monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", environment)
    assert local_client.get("/department").status_code == 404
    assert local_client.post("/department", data={"code": "X", "name": "X"}).status_code == 404


def test_untrusted_host_and_peer() -> None:
    for base_url, peer in [
        ("http://evil.example", "127.0.0.1"),
        ("http://localhost", "192.168.1.20"),
    ]:
        with TestClient(app, base_url=base_url, client=(peer, 50000)) as browser:
            assert browser.get("/department").status_code == 404


@pytest.mark.parametrize(
    "origin", [None, "https://evil.example", "https://invoiceguard-ai-admin.localhost:8080"]
)
def test_write_requires_exact_origin(local_client: TestClient, origin: str | None) -> None:
    headers = {"origin": origin} if origin else {}
    assert (
        local_client.post(
            "/department", data={"code": "X", "name": "X"}, headers=headers
        ).status_code
        == 403
    )


def test_create_feedback(local_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    create = MagicMock(return_value=ROW)
    monkeypatch.setattr(service, "create", create)
    headers = {"origin": "http://invoiceguard-ai-admin.localhost:8080"}
    assert (
        "已新增部門"
        in local_client.post(
            "/department", data={"code": "TEST_ONLY", "name": "虛構"}, headers=headers
        ).text
    )
    create.side_effect = service.DepartmentError("部門代碼已存在")
    response = local_client.post(
        "/department", data={"code": "TEST_ONLY", "name": "虛構"}, headers=headers
    )
    assert "部門代碼已存在" in response.text
    assert "已新增部門" not in response.text


def test_create_commit_and_normalize(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    create = MagicMock(return_value=ROW)
    monkeypatch.setattr(repository, "create", create)
    assert service.create(" TEST_ONLY ", " 虛構 ") == ROW
    create.assert_called_once_with(db, "TEST_ONLY", "虛構")
    db.commit.assert_called_once()
    db.close.assert_called_once()


@pytest.mark.parametrize("value", ["", "  ", "x\x00", "x" * 201])
def test_invalid_inputs_rejected(value: str) -> None:
    with pytest.raises(service.DepartmentError):
        service.create(value, "虛構")


def test_duplicate_rolls_back(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    def duplicate(*args: object) -> Department:
        raise IntegrityError("hidden", {}, SimpleNamespace(sqlstate="23505"))

    monkeypatch.setattr(repository, "create", duplicate)
    with pytest.raises(service.DepartmentError, match="部門代碼已存在"):
        service.create("TEST_ONLY", "虛構")
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
    db.close.assert_called_once()


def test_rename(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    rename = MagicMock(return_value=ROW)
    monkeypatch.setattr(repository, "rename", rename)
    service.rename(ID, "新虛構名稱")
    rename.assert_called_once_with(db, ID, "新虛構名稱")
    db.commit.assert_called_once()


@pytest.mark.parametrize("blocker", ["仍是主要部門", "仍有成員", "未知外鍵"])
def test_referenced_department_rejected(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch, blocker: str
) -> None:
    monkeypatch.setattr(repository, "lock_department", lambda *args: ROW)
    monkeypatch.setattr(repository, "disable_blocker", lambda *args: blocker)
    setter = MagicMock()
    monkeypatch.setattr(repository, "set_active", setter)
    with pytest.raises(service.DepartmentError, match=blocker):
        service.set_active(ID, False)
    setter.assert_not_called()
    db.rollback.assert_called_once()


@pytest.mark.parametrize("active", [False, True])
def test_status_commit(db: MagicMock, monkeypatch: pytest.MonkeyPatch, active: bool) -> None:
    monkeypatch.setattr(repository, "lock_department", lambda *args: ROW)
    monkeypatch.setattr(repository, "disable_blocker", lambda *args: None)
    setter = MagicMock(return_value=ROW)
    monkeypatch.setattr(repository, "set_active", setter)
    service.set_active(ID, active)
    setter.assert_called_once_with(db, ID, active)
    db.commit.assert_called_once()


def test_default_department_protected(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        repository,
        "lock_department",
        lambda *args: ROW.model_copy(update={"department_code": "UNASSIGNED"}),
    )
    with pytest.raises(service.DepartmentError, match="預設部門"):
        service.set_active(ID, False)
    db.commit.assert_not_called()


def test_database_error_is_sanitized(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    db.commit.side_effect = OperationalError("private sql", {}, Exception("private host"))
    monkeypatch.setattr(repository, "create", lambda *args: ROW)
    with pytest.raises(service.DepartmentError) as error:
        service.create("TEST_ONLY", "虛構")
    assert "private" not in str(error.value)
    db.rollback.assert_called_once()


@pytest.mark.parametrize(
    "primary,membership,expected",
    [(True, False, "主要部門"), (False, True, "使用者部門"), (False, False, None)],
)
def test_repository_reference_checks(primary: bool, membership: bool, expected: str | None) -> None:
    session = MagicMock()
    keys = MagicMock()
    keys.mappings.return_value = [
        {
            "schema_name": "public",
            "table_name": "user_departments",
            "source_columns": ["department_id"],
            "target_columns": ["department_id"],
        }
    ]
    used = MagicMock()
    used.mappings.return_value.one.return_value = {
        "primary_used": primary,
        "membership_used": membership,
    }
    session.execute.side_effect = [None, None, None, keys, used]
    result = repository.disable_blocker(session, ID)
    assert (expected in result) if expected else result is None


def test_repository_unknown_fk_rejected() -> None:
    session = MagicMock()
    keys = MagicMock()
    keys.mappings.return_value = [
        {
            "schema_name": "public",
            "table_name": "unknown_table",
            "source_columns": ["department_id"],
            "target_columns": ["department_id"],
        }
    ]
    session.execute.side_effect = [None, None, None, keys]
    assert "尚未核對" in repository.disable_blocker(session, ID)


def test_search_uses_bound_literal_query() -> None:
    session = MagicMock()
    session.execute.return_value.mappings.return_value = []
    query = "%' OR 1=1 --"
    assert repository.search(session, query) == []
    statement, params = session.execute.call_args.args
    assert params == {"q": query}
    assert query not in str(statement)


def test_change_code_preserves_identity(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository, "lock_department", lambda *args: ROW)
    monkeypatch.setattr(repository, "code_change_blocker", lambda db: None)
    changed = ROW.model_copy(update={"department_code": "finance_d"})
    setter = MagicMock(return_value=changed)
    monkeypatch.setattr(repository, "change_code", setter)
    result = service.change_code(ID, " finance_d ")
    assert result.department_id == ROW.department_id
    assert result.department_name == ROW.department_name
    setter.assert_called_once_with(db, ID, "finance_d")
    db.commit.assert_called_once()


@pytest.mark.parametrize("old,new", [("UNASSIGNED", "finance_d"), ("TEST_ONLY", "UNASSIGNED")])
def test_default_department_code_protected(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch, old: str, new: str
) -> None:
    monkeypatch.setattr(
        repository, "lock_department", lambda *args: ROW.model_copy(update={"department_code": old})
    )
    with pytest.raises(service.DepartmentError, match="保留"):
        service.change_code(ID, new)
    db.commit.assert_not_called()


def test_code_fk_blocks_before_write(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository, "lock_department", lambda *args: ROW)
    monkeypatch.setattr(repository, "code_change_blocker", lambda db: "部門代碼有外鍵引用")
    setter = MagicMock()
    monkeypatch.setattr(repository, "change_code", setter)
    with pytest.raises(service.DepartmentError, match="外鍵"):
        service.change_code(ID, "finance_d")
    setter.assert_not_called()
    db.rollback.assert_called_once()


def test_code_route_and_origin(local_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    setter = MagicMock(return_value=ROW)
    monkeypatch.setattr(service, "change_code", setter)
    response = local_client.post(
        f"/department/{ID}/code",
        data={"code": "finance_d"},
        headers={"Origin": "http://invoiceguard-ai-admin.localhost:8080"},
    )
    assert "已修改部門代碼" in response.text
    setter.assert_called_once_with(ID, "finance_d")
    assert (
        local_client.post(f"/department/{ID}/code", data={"code": "finance_d"}).status_code == 403
    )


def test_change_code_sql_updates_no_other_business_fields() -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value.one.return_value = ROW.model_dump()
    repository.change_code(db, ID, "finance_d")
    statement, params = db.execute.call_args.args
    assert "SET department_code = :code, updated_at = now()" in str(statement)
    assert "SET department_name" not in str(statement)
    assert params == {"id": ID, "code": "finance_d"}
