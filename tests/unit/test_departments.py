"""部門管理回歸測試；僅使用合成資料及替身，不連正式資料庫。"""

from collections.abc import Iterator
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
from app.routers.e_admin import require_configuration_access
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
def local_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """合成授權前提下的業務回歸；保留 Host 與同源保護，不代表正式開放。"""
    monkeypatch.setattr(service, "search", lambda q="": [ROW])
    # 僅隔離業務 UI；正式驗權與未接線拒絕行為由 test_e_login_access 驗證。
    monkeypatch.setitem(app.dependency_overrides, require_configuration_access, lambda: None)
    with TestClient(
        app, base_url="http://invoiceguard-ai-admin.localhost:8080", client=("127.0.0.1", 50000)
    ) as browser:
        yield browser


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


@pytest.mark.parametrize("blocker", ["仍是主要部門", "未知外鍵"])
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


@pytest.mark.parametrize("code", ["UNASSIGNED", "IGTEST_81d56327f9e647be", "finance_d"])
@pytest.mark.parametrize("active", [False, True])
def test_all_department_codes_use_shared_status_rules(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch, code: str, active: bool
) -> None:
    """預設代碼與一般部門共用啟停流程；只變更目標狀態。"""
    row = ROW.model_copy(update={"department_code": code})
    monkeypatch.setattr(repository, "lock_department", lambda *args: row)
    blocker = MagicMock(return_value=None)
    monkeypatch.setattr(repository, "disable_blocker", blocker)
    setter = MagicMock(return_value=row.model_copy(update={"is_active": active}))
    monkeypatch.setattr(repository, "set_active", setter)
    result = service.set_active(ID, active)
    setter.assert_called_once_with(db, ID, active)
    assert result.department_code == code and result.is_active == active
    assert blocker.call_count == (0 if active else 1)
    db.commit.assert_called_once()


@pytest.mark.parametrize("reason", ["仍是帳號主要部門", "尚未核對的外鍵"])
def test_unassigned_keeps_shared_database_protections(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    monkeypatch.setattr(
        repository,
        "lock_department",
        lambda *args: ROW.model_copy(update={"department_code": "UNASSIGNED"}),
    )
    monkeypatch.setattr(repository, "disable_blocker", lambda *args: reason)
    setter = MagicMock()
    monkeypatch.setattr(repository, "set_active", setter)
    with pytest.raises(service.DepartmentError, match=reason):
        service.set_active(ID, False)
    setter.assert_not_called()
    db.rollback.assert_called_once()
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
    [(True, False, "主要部門"), (False, True, None), (False, False, None)],
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


def test_list_status_roundtrip_retains_disabled_row(
    local_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真實 HTTP 送出停用與啟用；同筆資料留在列表，搜尋與顯示狀態保持一致。"""
    current = [ROW]
    queries = []

    def search(q: str = "") -> list[Department]:
        queries.append(q)
        return current

    def set_active(department_id: UUID, active: bool) -> Department:
        assert department_id == ID
        current[0] = current[0].model_copy(update={"is_active": active})
        return current[0]

    monkeypatch.setattr(service, "search", search)
    monkeypatch.setattr(service, "set_active", set_active)
    headers = {"Origin": "http://invoiceguard-ai-admin.localhost:8080"}
    page = local_client.get("/department", params={"q": "虛構"})
    assert 'role="switch"' in page.text and 'aria-checked="true"' in page.text
    assert '<th scope="col" class="e-department-list__state">啟用／停用</th>' in page.text
    for active in [False, True]:
        response = local_client.post(
            f"/department/{ID}/status",
            headers=headers,
            data={"active": str(active).lower(), "q": "虛構"},
        )
        assert response.status_code == 200
        assert ("已啟用部門" if active else "已停用部門") in response.text
        assert f'aria-checked="{str(active).lower()}"' in response.text
        assert 'value="虛構"' in response.text and queries[-1] == "虛構"
        assert f'href="/department/{ID}/edit"' in response.text
        assert current[0].department_code == ROW.department_code
        assert current[0].created_at == ROW.created_at


def test_status_rejected_keeps_actual_state_and_search(
    local_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    setter = MagicMock(side_effect=service.DepartmentError("此部門仍是帳號的主要部門，無法停用"))
    monkeypatch.setattr(service, "set_active", setter)
    response = local_client.post(
        f"/department/{ID}/status",
        headers={"Origin": "http://invoiceguard-ai-admin.localhost:8080"},
        data={"active": "false", "q": "虛構"},
    )
    assert "無法停用" in response.text and "已停用部門" not in response.text
    assert 'aria-checked="true"' in response.text and 'value="虛構"' in response.text
    setter.assert_called_once_with(ID, False)


def test_status_cross_origin_rejected_before_service(
    local_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    setter = MagicMock()
    monkeypatch.setattr(service, "set_active", setter)
    response = local_client.post(
        f"/department/{ID}/status",
        headers={"Origin": "https://example.invalid"},
        data={"active": "false"},
    )
    assert response.status_code == 403
    setter.assert_not_called()


# v1.3/v1.4 既有關聯：只變更 is_active，不刪除 UUID 或授權／歷史資料。
_SCHEMA_DEPARTMENT_KEYS = [
    ("public", "department_permissions", ["department_id"], ["department_id"]),
    ("invoiceguard_identity", "user_sessions", ["selected_department_id"], ["department_id"]),
    ("public", "invoices", ["department_id"], ["department_id"]),
    ("public", "rag_department_access", ["department_id"], ["department_id"]),
    (
        "invoiceguard_identity",
        "user_accounts",
        ["primary_department_id", "primary_department_active"],
        ["department_id", "is_active"],
    ),
]


@pytest.mark.parametrize("schema,table,source_columns,target_columns", _SCHEMA_DEPARTMENT_KEYS)
@pytest.mark.parametrize(
    "primary,membership,expected",
    [(False, False, None), (True, False, "主要部門"), (False, True, None)],
)
def test_schema_fk_reaches_actual_account_reference_check(
    schema: str,
    table: str,
    source_columns: list[str],
    target_columns: list[str],
    primary: bool,
    membership: bool,
    expected: str | None,
) -> None:
    """正常授權／歷史關聯不誤擋；主要部門仍保護，成員存在不阻擋。"""
    session = MagicMock()
    keys = MagicMock()
    keys.mappings.return_value = [
        dict(
            schema_name=schema,
            table_name=table,
            source_columns=source_columns,
            target_columns=target_columns,
        )
    ]
    used = MagicMock()
    used.mappings.return_value.one.return_value = {
        "primary_used": primary,
        "membership_used": membership,
    }
    session.execute.side_effect = [None, None, None, keys, used]
    result = repository.disable_blocker(session, ID)
    assert (expected in result) if expected else result is None
    session.execute.assert_any_call(repository.REFERENCES, {"id": ID})
    assert session.execute.call_count == 5
    session.commit.assert_not_called()


@pytest.mark.parametrize(
    "schema,table,source_columns,target_columns",
    [
        (
            "public",
            "department_permissions",
            ["department_id", "active"],
            ["department_id", "is_active"],
        ),
        ("other_schema", "department_permissions", ["department_id"], ["department_id"]),
        ("public", "department_permissions", ["department_id"], ["department_code"]),
    ],
)
def test_known_table_with_unreviewed_fk_signature_still_rejected(
    schema: str, table: str, source_columns: list[str], target_columns: list[str]
) -> None:
    """不能僅憑表名放行不同 schema 或引用啟用狀態／代碼的未知外鍵。"""
    session = MagicMock()
    keys = MagicMock()
    keys.mappings.return_value = [
        dict(
            schema_name=schema,
            table_name=table,
            source_columns=source_columns,
            target_columns=target_columns,
        )
    ]
    session.execute.side_effect = [None, None, None, keys]
    assert "尚未核對" in repository.disable_blocker(session, ID)
    session.commit.assert_not_called()


@pytest.mark.parametrize("has_members", [False, True])
def test_disable_with_full_schema_fk_set_only_changes_department_status(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch, has_members: bool
) -> None:
    """有無成員均可停用；整套已核對外鍵存在時也只更新狀態、不刪關聯。"""
    monkeypatch.setattr(repository, "lock_department", lambda *args: ROW)
    keys = MagicMock()
    keys.mappings.return_value = [
        dict(
            schema_name=schema,
            table_name=table,
            source_columns=source_columns,
            target_columns=target_columns,
        )
        for schema, table, source_columns, target_columns in _SCHEMA_DEPARTMENT_KEYS
    ] + [
        dict(
            schema_name="public",
            table_name="user_departments",
            source_columns=["department_id"],
            target_columns=["department_id"],
        )
    ]
    used = MagicMock()
    used.mappings.return_value.one.return_value = {
        "primary_used": False,
        "membership_used": has_members,
    }
    updated = MagicMock()
    updated.mappings.return_value.one.return_value = ROW.model_copy(
        update={"is_active": False}
    ).model_dump()
    db.execute.side_effect = [None, None, None, keys, used, updated]
    result = service.set_active(ID, False)
    assert result.department_id == ID and not result.is_active
    assert db.execute.call_args.args == (repository.STATUS, {"id": ID, "active": False})
    writes = [
        str(call.args[0]).strip()
        for call in db.execute.call_args_list
        if str(call.args[0]).strip().split()[0].upper() in {"UPDATE", "INSERT", "DELETE"}
    ]
    assert writes == [str(repository.STATUS).strip()]
    db.commit.assert_called_once()
    db.rollback.assert_not_called()
