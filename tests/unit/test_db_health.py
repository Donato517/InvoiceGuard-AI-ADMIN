"""測試目標：暫時 DB 連線檢查（/health/db）唯讀 SELECT 1、只回固定欄位、不洩漏連線資訊；全部使用替身，不連 DB。"""

import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, call

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import ArgumentError, DBAPIError, OperationalError

from app.db.repositories import db_health_repository as repository
from app.routers import health
from app.services import db_health_service as service

SECRET = "synthetic-password-never-logged"
CLOUD_URL = "postgresql://u:" + SECRET + "@db.example.invalid:6543/d?sslmode=require"


@pytest.fixture(autouse=True)
def clear_confirmed_result() -> Iterator[None]:
    """成功結果每個執行個體快取一次；測試間清除。"""
    service._confirmed_select_one.cache_clear()
    yield
    service._confirmed_select_one.cache_clear()


def wire(monkeypatch: pytest.MonkeyPatch, app_env: str, database_url: str, check: Any) -> None:
    """路由與 service 改用合成設定及替身檢查。"""
    for module in (health, service):
        monkeypatch.setattr(module.settings, "APP_ENV", app_env)
        # 與實際設定相同型別（SecretStr），避免測試掩蓋型別不符
        monkeypatch.setattr(module.settings, "DATABASE_URL", SecretStr(database_url))
    monkeypatch.setattr(service, "check_connection", check)


def diagnostic_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """只取診斷 service 的日誌，忽略 TestClient 自身的請求紀錄。"""
    return [record.getMessage() for record in caplog.records if record.name == service.__name__]


def test_local_endpoint_is_404_without_db_attempt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = MagicMock()
    wire(monkeypatch, "local", "", check)
    assert client.get("/health/db").status_code == 404
    check.assert_not_called()


@pytest.mark.parametrize("app_env", ["preview", "production"])
def test_cloud_returns_and_logs_env_url_presence_and_select_one(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    app_env: str,
) -> None:
    check = MagicMock(return_value=1)
    wire(monkeypatch, app_env, CLOUD_URL, check)
    with caplog.at_level(logging.INFO, logger=service.__name__):
        response = client.get("/health/db", params={"sql": "DELETE FROM users"})
    assert response.status_code == 200
    assert list(response.json().items()) == [
        ("app_env", app_env),
        ("database_url_present", True),
        ("select_1", 1),
        ("status", "DB_OK"),
    ]
    assert response.headers["cache-control"] == "no-store"
    assert diagnostic_lines(caplog) == [
        f"DB_DIAGNOSTIC app_env={app_env} database_url_present=True select_1=1 status=DB_OK"
    ]
    assert SECRET not in caplog.text + response.text
    check.assert_called_once_with()


def test_failure_returns_and_logs_fixed_code_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail() -> int:
        raise service.HealthCheckError("configuration_unavailable")

    wire(monkeypatch, "production", "", fail)
    with caplog.at_level(logging.INFO, logger=service.__name__):
        response = client.get("/health/db")
    assert response.status_code == 503
    assert response.json() == {
        "app_env": "production",
        "database_url_present": False,
        "status": "DB_ERROR",
        "code": "configuration_unavailable",
    }
    assert diagnostic_lines(caplog) == [
        "DB_DIAGNOSTIC app_env=production database_url_present=False"
        " status=DB_ERROR code=configuration_unavailable"
    ]


def test_success_connects_once_per_instance_but_failure_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = MagicMock(side_effect=[service.HealthCheckError("connect_timeout"), 1, 1])
    wire(monkeypatch, "production", CLOUD_URL, attempts)
    statuses = [service.connection_status()["status"] for _ in range(3)]
    assert statuses == ["DB_ERROR", "DB_OK", "DB_OK"]
    assert attempts.call_count == 2


def test_service_closes_session_and_never_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MagicMock()
    monkeypatch.setattr(repository, "open_session", lambda: session)
    monkeypatch.setattr(repository, "ping", MagicMock(return_value=1))
    assert service.check_connection() == 1
    session.close.assert_called_once()
    session.commit.assert_not_called()


def test_service_keeps_ping_error_when_close_also_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MagicMock()
    session.close.side_effect = RuntimeError(SECRET)

    def fail(_: Any) -> int:
        raise repository.HealthCheckError("auth_failed")

    monkeypatch.setattr(repository, "open_session", lambda: session)
    monkeypatch.setattr(repository, "ping", fail)
    with pytest.raises(service.HealthCheckError) as caught:
        service.check_connection()
    assert caught.value.code == "auth_failed"


def test_service_redacts_unexpected_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode() -> None:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(repository, "open_session", explode)
    with pytest.raises(service.HealthCheckError) as caught:
        service.check_connection()
    assert caught.value.code == "query_failed"
    assert SECRET not in str(caught.value)


def test_open_session_uses_existing_engine_and_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = object()
    factory = MagicMock()
    monkeypatch.setattr(repository, "get_engine", lambda: engine)
    monkeypatch.setattr(repository, "SessionLocal", factory)
    assert repository.open_session() is factory.return_value
    factory.assert_called_once_with(bind=engine)


def test_invalid_database_url_is_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> None:
        raise ArgumentError("synthetic")

    monkeypatch.setattr(repository, "get_engine", fail)
    with pytest.raises(repository.HealthCheckError) as caught:
        repository.open_session()
    assert caught.value.code == "configuration_unavailable"


def test_ping_connects_then_runs_only_read_only_select_one() -> None:
    session = MagicMock()
    session.execute.return_value.scalar_one.return_value = 1
    assert repository.ping(session) == 1
    assert session.mock_calls[0] == call.connection()
    statements = [str(item.args[0]) for item in session.execute.call_args_list]
    assert statements == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL statement_timeout = '4000ms'",
        "SELECT 1",
    ]
    session.commit.assert_not_called()


@pytest.mark.parametrize("state,code", [("57014", "query_timeout"), ("42501", "query_failed")])
def test_ping_query_errors_use_sqlstate_only(state: str, code: str) -> None:
    original = Exception(SECRET)
    original.sqlstate = state  # type: ignore[attr-defined]
    session = MagicMock()
    session.execute.side_effect = DBAPIError("SELECT 1", None, original)
    with pytest.raises(repository.HealthCheckError) as caught:
        repository.ping(session)
    assert caught.value.code == code
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "message,code",
    [
        ('FATAL:  password authentication failed for user "u"', "auth_failed"),
        ("FATAL:  Tenant or user not found", "auth_failed"),
        ('FATAL:  database "d" does not exist', "database_not_found"),
        ('FATAL:  no pg_hba.conf entry for host "1.2.3.4"', "access_denied"),
        (
            'could not translate host name "h" to address: Name or service not known',
            "host_not_found",
        ),
        ("connection to server failed: Network is unreachable", "network_unreachable"),
        ("connection timeout expired", "connect_timeout"),
        ("server does not support SSL, but SSL was required", "tls_failed"),
        ("FATAL:  sorry, too many connections already", "too_many_connections"),
        ("Connection refused", "db_unavailable"),
    ],
)
def test_connect_failures_map_to_fixed_codes_without_echo(message: str, code: str) -> None:
    session = MagicMock()
    session.connection.side_effect = OperationalError(None, None, Exception(message + SECRET))
    with pytest.raises(repository.HealthCheckError) as caught:
        repository.ping(session)
    assert caught.value.code == code
    assert SECRET not in str(caught.value)
    session.execute.assert_not_called()
