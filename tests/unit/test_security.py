"""測試目標：app.core.security（登入 cookie 簽署、密碼雜湊、防 open redirect）"""

import pytest

from app.core.config import settings
from app.core.security import (
    create_session_token,
    hash_password,
    read_session_token,
    safe_next_path,
    verify_password,
)


@pytest.fixture(autouse=True)
def session_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test-secret-key-for-unit-tests")


def test_session_token_round_trip() -> None:
    token = create_session_token(42)

    assert read_session_token(token) == 42


def test_tampered_session_token_is_rejected() -> None:
    token = create_session_token(42)

    assert read_session_token(token[:-2] + "xx") is None


def test_expired_session_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    token = create_session_token(42)
    monkeypatch.setattr(settings, "SESSION_MAX_AGE_SECONDS", -1)

    assert read_session_token(token) is None


def test_session_token_requires_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "")

    with pytest.raises(RuntimeError):
        create_session_token(42)


def test_password_hash_and_verify() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", password_hash)
    assert not verify_password("wrong password", password_hash)


@pytest.mark.parametrize(
    ("next_path", "expected"),
    [
        ("/invoices", "/invoices"),
        (None, "/dashboard"),
        ("https://evil.example.com", "/dashboard"),
        ("//evil.example.com", "/dashboard"),
        ("/\\evil.example.com", "/dashboard"),
    ],
)
def test_safe_next_path(next_path: str | None, expected: str) -> None:
    assert safe_next_path(next_path) == expected
