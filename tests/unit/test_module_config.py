"""模組名稱：tests.unit.test_module_config；功能說明：僅使用假值測試設定與機密邊界。"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.module_errors import ModuleConfigurationError
from app.integrations import vercel

FAKE_DATABASE_URL = (
    "postgresql://test_user:synthetic-password@db.example.invalid:6543/invoiceguard?sslmode=require"
)


def make_settings(**values: Any) -> Settings:
    """以隔離的環境前綴及假值建構，不讀取 .env 或公司環境變數。"""
    return Settings(_env_file=None, _env_prefix="IG_UNIT_TEST_", **values)


def cloud_settings(**values: Any) -> Settings:
    """建立不會連網的雲端設定替身。"""
    defaults = {
        "VERCEL": "1",
        "APP_ENV": "preview",
        "DATABASE_URL": FAKE_DATABASE_URL,
        "SUPABASE_URL": "https://storage.example.invalid",
        "SUPABASE_STORAGE_BUCKET": "test-invoices",
        "SUPABASE_SECRET_KEY": "synthetic-storage-key-not-a-company-key",
        "SESSION_SECRET_KEY": "synthetic-session-key-for-tests-only-1234",
    }
    return make_settings(**(defaults | values))


def test_local_needs_no_company_secrets() -> None:
    report = make_settings().check_configuration("local")
    assert report.is_valid
    assert not report.as_dict()["connectivity_checked"]
    assert not report.as_dict()["operations_implemented"]
    assert {issue.code for issue in report.issues} == {"remote_not_configured", "contracts_only"}


def test_dotenv_is_not_loaded_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=synthetic-dotenv-value\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert Settings().DATABASE_URL.get_secret_value() == ""


@pytest.mark.parametrize(
    "name",
    [
        "DATABASE_URL",
        "SESSION_SECRET_KEY",
        "SUPABASE_SECRET_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
    ],
)
def test_local_rejects_company_secret_fields(name: str) -> None:
    report = make_settings(**{name: "synthetic-secret"}).check_configuration("local")
    assert not report.is_valid
    assert any(
        issue.field == name and issue.code == "cloud_secret_in_local" for issue in report.issues
    )
    assert "synthetic-secret" not in json.dumps(report.as_dict())


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid",
        "https://user:password@example.invalid",
        "https://example.invalid?token=synthetic",
        "https://example.invalid/#fragment",
        "https://example.invalid:bad",
        "https://example.invalid\\path",
    ],
)
def test_remote_url_rejects_unsafe_inputs(url: str) -> None:
    assert not make_settings(REMOTE_ADMIN_BASE_URL=url).check_configuration("local").is_valid


def test_remote_url_accepts_https_base_without_credentials() -> None:
    assert (
        make_settings(REMOTE_ADMIN_BASE_URL="https://admin.example.invalid")
        .check_configuration("local")
        .is_valid
    )


def test_complete_fake_cloud_configuration_is_only_static_validation() -> None:
    report = cloud_settings().check_configuration("cloud")
    assert report.is_valid
    assert report.as_dict()["operations_implemented"] is False


@pytest.mark.parametrize(
    "values", [{"VERCEL": ""}, {"APP_ENV": "local"}, {"DEBUG": True}, {"MODULE_BACKEND": "remote"}]
)
def test_cloud_requires_explicit_safe_execution_context(values: dict[str, Any]) -> None:
    assert not cloud_settings(**values).check_configuration("cloud").is_valid


@pytest.mark.parametrize(
    "url",
    [
        "",
        "postgresql://",
        "sqlite:///test.db",
        "postgresql://user:password@host",
        "postgresql://user@host/db",
        "postgresql://user:password@host:bad/db",
        "postgresql://user:password@host:99999/db",
    ],
)
def test_database_requires_complete_postgres_url(url: str) -> None:
    configuration = cloud_settings(DATABASE_URL=url)
    with pytest.raises(ModuleConfigurationError) as caught:
        configuration.require_configuration("database")
    assert "invalid_postgres_url" in str(caught.value)
    assert not url or url not in str(caught.value)


@pytest.mark.parametrize("prefix", ["postgres://", "postgresql://", "postgresql+psycopg://"])
def test_database_scheme_is_normalized(prefix: str) -> None:
    configuration = cloud_settings(DATABASE_URL=prefix + "user:synthetic@db.example.invalid/test")
    assert configuration.check_configuration("database").is_valid
    assert configuration.sqlalchemy_database_url.startswith("postgresql+psycopg://")


@pytest.mark.parametrize("mode", ["disable", "allow", "prefer"])
def test_database_rejects_tls_downgrade(mode: str) -> None:
    url = FAKE_DATABASE_URL.replace("sslmode=require", "sslmode=" + mode)
    report = cloud_settings(DATABASE_URL=url).check_configuration("database")
    assert any(issue.code == "unsafe_ssl_mode" for issue in report.issues)


@pytest.mark.parametrize(
    "field,value",
    [
        ("SUPABASE_URL", "http://storage.example.invalid"),
        ("SUPABASE_STORAGE_BUCKET", "../other"),
        ("SUPABASE_SECRET_KEY", ""),
        ("SESSION_SECRET_KEY", "short"),
    ],
)
def test_cloud_checks_each_required_capability(field: str, value: str) -> None:
    assert not cloud_settings(**{field: value}).check_configuration("cloud").is_valid


def test_settings_and_reports_do_not_print_secrets() -> None:
    configuration = cloud_settings(
        GEMINI_API_KEY="synthetic-gemini", OPENAI_API_KEY="synthetic-openai"
    )
    output = (
        repr(configuration)
        + configuration.model_dump_json()
        + json.dumps(configuration.check_configuration("cloud").as_dict())
    )
    for secret in (
        "synthetic-password",
        "synthetic-storage-key",
        "synthetic-session-key",
        "synthetic-gemini",
        "synthetic-openai",
    ):
        assert secret not in output


def test_unknown_config_fields_cannot_hide_secrets() -> None:
    with pytest.raises(ValidationError) as caught:
        make_settings(ARBITRARY_PUBLIC_KEY="synthetic-secret-marker")
    assert "synthetic-secret-marker" not in str(caught.value)


@pytest.mark.parametrize(
    "values",
    [
        {"APP_ENV": "invalid"},
        {"MODULE_BACKEND": "invalid"},
        {"DB_SSL_MODE": "disable"},
        {"DB_CONNECT_TIMEOUT_SECONDS": 0},
    ],
)
def test_invalid_configuration_types_are_rejected(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**values)


def test_vercel_local_misconfiguration_still_uses_secure_cookie() -> None:
    assert make_settings(VERCEL="1", APP_ENV="local").cookie_secure


def test_platform_descriptor_contains_no_settings_or_clients() -> None:
    platform = vercel.get_platform(cloud_settings())
    assert platform.backend == "cloud"
    assert platform.operations_implemented is False
    assert "synthetic" not in repr(platform)


def test_cli_reports_no_connection_and_returns_exit_codes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(vercel, "settings", make_settings())
    assert vercel.main(["--check", "local"]) == 0
    assert json.loads(capsys.readouterr().out)["connectivity_checked"] is False
    assert vercel.main(["--check", "cloud"]) == 2
    assert json.loads(capsys.readouterr().out)["configuration_valid"] is False
