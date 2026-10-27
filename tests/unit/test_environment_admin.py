"""ADMIN 環境隔離接線測試；只使用假網址，不連線、不需要公司密鑰。"""

import importlib
from typing import Any

import pytest

from app.core.config import Settings
from app.core.environment import EnvironmentConfigurationError


def make_settings(**values: Any) -> Settings:
    return Settings(_env_prefix="IG_UNIT_TEST_", **values)


def test_local_environment_without_company_secrets_is_valid() -> None:
    configuration = make_settings()
    assert configuration.check_environment()["configuration_valid"] is True
    configuration.require_environment()


def test_module_report_does_not_duplicate_environment_issues() -> None:
    configuration = make_settings(DATABASE_URL="synthetic-local-secret-not-a-company-value")
    report = configuration.check_configuration("local")
    pairs = [(issue.field, issue.code) for issue in report.issues]
    assert ("DATABASE_URL", "invalid_postgres_url") in pairs
    assert ("DATABASE_URL", "cloud_secret_in_local") not in pairs
    assert len(pairs) == len(set(pairs))


@pytest.mark.parametrize(
    "values",
    [
        {"VERCEL": "1", "VERCEL_ENV": "production", "APP_ENV": "local"},
        {"VERCEL": "1", "VERCEL_ENV": "production", "APP_ENV": "preview"},
        {"VERCEL": "1", "APP_ENV": "production"},
        {"APP_ENV": "production"},
    ],
)
def test_module_configuration_includes_environment_failures(values: dict[str, Any]) -> None:
    configuration = make_settings(**values)
    assert configuration.check_environment()["configuration_valid"] is False
    assert configuration.check_configuration("session").is_valid is False
    with pytest.raises(EnvironmentConfigurationError):
        configuration.require_environment()


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://localhost",
        "https://temporary.trycloudflare.com",
    ],
)
def test_admin_remote_endpoint_cannot_be_a_local_or_tunnel_service(url: str) -> None:
    configuration = make_settings(REMOTE_ADMIN_BASE_URL=url)
    assert configuration.check_configuration("local").is_valid is False


def test_main_refuses_conflicting_environment_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import main
    from app.core.config import settings

    original_app = main.app
    with monkeypatch.context() as context:
        context.setattr(settings, "VERCEL", "1")
        context.setattr(settings, "VERCEL_ENV", "production")
        context.setattr(settings, "APP_ENV", "local")
        with pytest.raises(EnvironmentConfigurationError):
            importlib.reload(main)
    assert main.app is original_app
