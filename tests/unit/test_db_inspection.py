"""模組名稱：tests.unit.test_db_inspection；功能說明：以替身驗證授權、唯讀、固定目標及去敏錯誤，不連 DB。"""

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.core import deps
from app.core.module_errors import ConfigurationIssue, ConfigurationReport, ModuleConfigurationError
from app.db.repositories import db_inspection_repository as repository
from app.db.session import get_db
from app.main import app
from app.schemas.db_inspection import DatabaseInspection, InspectedTable
from app.services import db_inspection_service as service


def make_report() -> DatabaseInspection:
    """建立不含任何資料列內容的合成盤點結果。"""
    return DatabaseInspection(
        table=InspectedTable(
            schema_name="public",
            name="user_profiles",
            exists=True,
            row_count=0,
            count_status="exact",
        )
    )


@pytest.fixture
def connection() -> Iterator[MagicMock]:
    """替身連線依序回傳 SELECT 1、catalog、欄位及筆數。"""
    db = MagicMock()
    ping = MagicMock()
    ping.scalar_one.return_value = 1
    relation = MagicMock()
    relation.mappings.return_value.one_or_none.return_value = {
        "relation_id": 42,
        "relkind": "r",
        "schema_usage": True,
        "can_select": True,
        "rls_active": False,
        "has_children": False,
    }
    columns = MagicMock()
    columns.mappings.return_value = [{"name": "user_id", "data_type": "uuid", "nullable": False}]
    count = MagicMock()
    count.scalar_one.return_value = 0
    db.execute.side_effect = [ping, relation, columns, count]
    yield db


def test_unauthenticated_request_redirects_before_inspection_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = MagicMock()
    monkeypatch.setattr(repository, "open_connection", factory)
    response = client.get("/system/db-inspection")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fsystem%2Fdb-inspection"
    assert "www-authenticate" not in response.headers
    factory.assert_not_called()


def test_non_admin_keeps_existing_website_authorization(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = MagicMock()
    monkeypatch.setattr(repository, "open_connection", factory)
    monkeypatch.setattr(
        deps.user_repository,
        "get_by_id",
        lambda db, user_id: SimpleNamespace(is_active=True, role="user"),
    )
    app.dependency_overrides[deps.get_session_user_id] = lambda: 2
    app.dependency_overrides[get_db] = lambda: None
    try:
        response = client.get("/system/db-inspection")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
    factory.assert_not_called()


def test_admin_receives_only_safe_json_without_cache(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "inspect_table", lambda target: make_report())
    response = admin_client.get("/system/db-inspection")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.json() == {
        "service": "InvoiceGuard-AI-ADMIN",
        "read_only": True,
        "connection": {"status": "ok"},
        "table": {
            "schema": "public",
            "name": "user_profiles",
            "exists": True,
            "columns": [],
            "row_count": 0,
            "count_status": "exact",
        },
    }


@pytest.mark.parametrize(
    "target",
    [
        "auth.users",
        "public.user_profiles; DROP TABLE x",
        "public.user_profiles -- marker",
        "public.unknown",
        "PUBLIC.user_profiles",
        "user_profiles",
    ],
)
def test_unapproved_targets_never_open_connection_or_echo_input(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    factory = MagicMock()
    monkeypatch.setattr(repository, "open_connection", factory)
    response = admin_client.get("/system/db-inspection", params={"table": target})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "target_not_allowed"
    assert target not in response.text
    factory.assert_not_called()


def test_missing_database_url_returns_configuration_error(
    admin_client: TestClient,
) -> None:
    # 缺少 DATABASE_URL 時仍拒絕；不以 local 環境作為拒絕理由。
    response = admin_client.get("/system/db-inspection")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "configuration_unavailable"


def test_repository_uses_catalog_parameters_and_fixed_count(connection: MagicMock) -> None:
    report = repository.inspect_table(connection, "public.user_profiles")
    assert report.table.exists is True
    assert report.table.row_count == 0
    assert report.table.count_status == "exact"
    assert report.table.columns[0].model_dump() == {
        "name": "user_id",
        "data_type": "uuid",
        "nullable": False,
    }
    calls = connection.execute.call_args_list
    assert str(calls[0].args[0]) == "SELECT 1"
    assert calls[1].args[1] == {"schema": "public", "table": "user_profiles"}
    assert calls[2].args[1] == {"relation_id": 42}
    assert str(calls[3].args[0]) == 'SELECT count(*) FROM ONLY "public"."user_profiles"'
    query_text = " ".join(str(call.args[0]) for call in calls).lower()
    assert "password_hash" not in query_text
    assert "column_default" not in query_text
    assert "pg_get_expr" not in query_text
    assert "select *" not in query_text


def test_missing_table_is_distinct_from_zero_and_never_counts(connection: MagicMock) -> None:
    ping = MagicMock()
    ping.scalar_one.return_value = 1
    relation = MagicMock()
    relation.mappings.return_value.one_or_none.return_value = None
    connection.execute.side_effect = [ping, relation]
    report = repository.inspect_table(connection, "public.user_profiles")
    assert report.table.exists is False
    assert report.table.row_count is None
    assert report.table.count_status == "missing"
    assert report.table.columns == []
    assert connection.execute.call_count == 2


@pytest.mark.parametrize(
    "change,status",
    [
        ({"rls_active": True}, "rls_active"),
        ({"can_select": False}, "permission_denied"),
        ({"schema_usage": False}, "permission_denied"),
        ({"relkind": "v"}, "unsupported_relation"),
        ({"relkind": "f"}, "unsupported_relation"),
        ({"relkind": "p"}, "unsupported_relation"),
        ({"has_children": True}, "unsupported_relation"),
    ],
)
def test_unavailable_counts_never_query_rows_or_return_false_zero(
    connection: MagicMock, change: dict[str, Any], status: str
) -> None:
    ping = MagicMock()
    ping.scalar_one.return_value = 1
    relation = MagicMock()
    relation.mappings.return_value.one_or_none.return_value = {
        "relation_id": 42,
        "relkind": "r",
        "schema_usage": True,
        "can_select": True,
        "rls_active": False,
        "has_children": False,
    } | change
    columns = MagicMock()
    columns.mappings.return_value = []
    connection.execute.side_effect = [ping, relation, columns]
    report = repository.inspect_table(connection, "public.user_profiles")
    assert report.table.exists is True
    assert report.table.row_count is None
    assert report.table.count_status == status
    assert connection.execute.call_count == 3


def test_prepare_is_first_readonly_statement_then_bounded_timeouts() -> None:
    db = MagicMock()
    repository.prepare_read_only(db)
    assert [str(call.args[0]) for call in db.execute.call_args_list] == [
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        "SET LOCAL statement_timeout = '4000ms'",
        "SET LOCAL lock_timeout = '1000ms'",
        "SET LOCAL row_security = off",
    ]
    statements = " ".join(str(call.args[0]) for call in db.execute.call_args_list)
    assert "row_security = on" not in statements
    assert "BYPASSRLS" not in statements


@pytest.mark.parametrize("fail_query", [False, True])
def test_service_always_rolls_back_before_closing(
    monkeypatch: pytest.MonkeyPatch, fail_query: bool
) -> None:
    events: list[str] = []
    db = MagicMock()
    transaction = MagicMock()
    db.begin.side_effect = lambda: events.append("begin") or transaction
    transaction.rollback.side_effect = lambda: events.append("rollback")
    db.close.side_effect = lambda: events.append("close")
    monkeypatch.setattr(repository, "open_connection", lambda: events.append("open") or db)
    monkeypatch.setattr(repository, "prepare_read_only", lambda db: events.append("prepare"))

    def inspect(db: Any, target: str) -> DatabaseInspection:
        events.append("inspect")
        if fail_query:
            raise repository.InspectionError("query_timeout")
        return make_report()

    monkeypatch.setattr(repository, "inspect_table", inspect)
    if fail_query:
        with pytest.raises(repository.InspectionError, match="query_timeout"):
            service.inspect_table("public.user_profiles")
    else:
        assert service.inspect_table("public.user_profiles").table.row_count == 0
    assert events == ["open", "begin", "prepare", "inspect", "rollback", "close"]
    db.commit.assert_not_called()
    transaction.commit.assert_not_called()


def test_service_rejects_unapproved_target_before_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = MagicMock()
    monkeypatch.setattr(repository, "open_connection", factory)
    with pytest.raises(repository.InspectionError, match="target_not_allowed"):
        service.inspect_table("auth.users")
    factory.assert_not_called()


@pytest.mark.parametrize("failure", ["begin", "rollback", "close"])
def test_cleanup_or_begin_failure_is_redacted(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    db = MagicMock()
    transaction = db.begin.return_value
    target = transaction if failure == "rollback" else db
    getattr(target, failure).side_effect = RuntimeError("synthetic-secret-connection-marker")
    monkeypatch.setattr(repository, "open_connection", lambda: db)
    monkeypatch.setattr(repository, "prepare_read_only", lambda db: None)
    monkeypatch.setattr(repository, "inspect_table", lambda db, table: make_report())
    with pytest.raises(repository.InspectionError) as caught:
        service.inspect_table("public.user_profiles")
    assert str(caught.value) == "query_failed"
    assert "synthetic-secret" not in str(caught.value)
    db.close.assert_called_once()


@pytest.mark.parametrize("state,code", [("57014", "query_timeout"), ("42501", "query_failed")])
def test_query_error_never_returns_sql_or_driver_secret(state: str, code: str) -> None:
    original = RuntimeError("synthetic-secret-connection-marker")
    original.sqlstate = state  # type: ignore[attr-defined]
    error = DBAPIError("SELECT synthetic-secret", {"secret": "marker"}, original)
    db = MagicMock()
    db.execute.side_effect = error
    with pytest.raises(repository.InspectionError) as caught:
        repository.inspect_table(db, "public.user_profiles")
    assert str(caught.value) == code
    assert caught.value.__suppress_context__ is True


def test_configuration_error_is_redacted_before_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = ModuleConfigurationError(
        ConfigurationReport(
            "database", (ConfigurationIssue("DATABASE_URL", "required", "固定測試訊息"),)
        )
    )
    factory = MagicMock(side_effect=failure)
    monkeypatch.setattr(repository, "get_engine", factory)
    with pytest.raises(repository.InspectionError, match="configuration_unavailable"):
        repository.open_connection()


def test_service_error_response_does_not_echo_exception_or_log_secret(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = MagicMock()
    db.begin.side_effect = RuntimeError("synthetic-secret-connection-marker")
    monkeypatch.setattr(repository, "open_connection", lambda: db)
    response = admin_client.get("/system/db-inspection")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "query_failed"
    assert "synthetic-secret" not in response.text
    assert "synthetic-secret" not in caplog.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "target,query",
    [
        ("public.user_profiles", 'SELECT count(*) FROM ONLY "public"."user_profiles"'),
        ("public.departments", 'SELECT count(*) FROM ONLY "public"."departments"'),
        ("public.user_departments", 'SELECT count(*) FROM ONLY "public"."user_departments"'),
        ("public.users", 'SELECT count(*) FROM ONLY "public"."users"'),
        (
            "invoiceguard_identity.user_accounts",
            'SELECT count(*) FROM ONLY "invoiceguard_identity"."user_accounts"',
        ),
    ],
)
def test_every_approved_target_uses_exact_fixed_count_statement(
    connection: MagicMock, target: str, query: str
) -> None:
    report = repository.inspect_table(connection, target)
    assert report.table.count_status == "exact"
    assert str(connection.execute.call_args_list[-1].args[0]) == query
    schema, table = target.split(".")
    assert connection.execute.call_args_list[1].args[1] == {"schema": schema, "table": table}


def test_relation_query_checks_permissions_only_for_tables() -> None:
    query = str(repository.RELATION_QUERY)
    assert query.count("CASE WHEN c.relkind IN ('r', 'p')") == 2
    assert "pg_catalog.pg_inherits" in query
    assert "i.inhparent = c.oid" in query


def test_authorized_local_inspection_reaches_db_module_with_provided_settings(
    admin_client: TestClient, connection: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import Settings
    from app.db import session

    configuration = Settings(
        _env_prefix="IG_UNIT_TEST_",
        APP_ENV="local",
        DATABASE_URL="postgresql://synthetic:fake-only@db.example.invalid/test?sslmode=require",
    )
    factory = MagicMock()
    factory.return_value.connect.return_value = connection
    query_results = list(connection.execute.side_effect)
    connection.execute.side_effect = [None] * 4 + query_results
    monkeypatch.setattr(session, "settings", configuration)
    monkeypatch.setattr(session, "create_engine", factory)
    session._get_engine.cache_clear()
    try:
        response = admin_client.get(
            "/system/db-inspection", params={"table": "public.user_profiles"}
        )
        assert response.status_code == 200
        assert response.json()["connection"] == {"status": "ok"}
        assert response.json()["table"]["count_status"] == "exact"
        assert response.json()["table"]["row_count"] == 0
        factory.assert_called_once()
        connection.begin.return_value.rollback.assert_called_once()
        connection.close.assert_called_once()
    finally:
        session._get_engine.cache_clear()
