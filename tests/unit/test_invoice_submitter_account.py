"""
模組名稱：tests.unit.test_invoice_submitter_account
功能說明：發票服務讀取上傳者帳號的退路——帳號查詢失敗只回「未提供」，不讓清單／詳情失敗，也不記錄 Email。
相依模組：app.services.invoice_review_service、app.db.repositories.invoice_repository
注意事項：用替身 Session，不連資料庫；真實 PostgreSQL 的權限／RLS 情境另以隔離資料庫驗證。
"""

import logging
from typing import Any

import pytest

from app.db.repositories import invoice_repository as repo
from app.services import invoice_review_service as service

UID = "33333333-3333-4333-8333-333333333333"
ROW = {
    "invoice_id": "44444444-4444-4444-8444-444444444444",
    "status": "pending_accounting_review",
    "invoice_track": "QU",
    "invoice_number": "08904069",
    "submitted_by": UID,
    "submitter_name": "測試人員",
}


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Savepoint:
    def __init__(self, db: "_Db") -> None:
        self.db = db

    def __enter__(self) -> "_Savepoint":
        self.db.savepoints += 1
        return self

    def __exit__(self, exc_type: Any, *_: Any) -> None:
        if exc_type is not None:
            self.db.rollbacks += 1  # 回到 savepoint（相當於 ROLLBACK TO SAVEPOINT）


class _Db:
    def __init__(self, accounts: list[dict[str, Any]] | Exception) -> None:
        self.accounts = accounts
        self.rollbacks = 0
        self.savepoints = 0

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        if "user_accounts" in sql:
            if isinstance(self.accounts, Exception):
                raise self.accounts
            return _Result(self.accounts)
        if "invoice_items" in sql:
            return _Result([])
        return _Result([dict(ROW)])

    def close(self) -> None:
        pass

    def begin_nested(self) -> "_Savepoint":
        return _Savepoint(self)


def _list(db: Any, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    monkeypatch.setattr(repo, "try_get_db_session", lambda: db)
    return service.fetch_invoices(is_finance=True) or []


def _detail(db: Any, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any] | None:
    monkeypatch.setattr(repo, "try_get_db_session", lambda: db)
    return service.fetch_invoice(ROW["invoice_id"])


def test_account_lookup_success_shows_prefix_only(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _Db([{"user_id": UID, "email": "rayneli2524@example.com"}])
    listed = _list(db, monkeypatch)
    detail = _detail(db, monkeypatch)
    assert listed[0]["submitter_account"] == "rayneli2524"
    assert detail is not None and detail["submitter_account"] == "rayneli2524"
    assert "example.com" not in str(listed) + str(detail)


def test_account_without_email_row_is_not_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    listed = _list(_Db([]), monkeypatch)
    assert listed[0]["submitter_account"] == "未提供"


def test_account_lookup_error_does_not_fail_pages_or_log_email(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.WARNING)
    db = _Db(
        repo.DatabaseError("permission denied for table user_accounts rayneli2524@example.com")
    )
    listed = _list(db, monkeypatch)
    detail = _detail(db, monkeypatch)
    assert listed[0]["invoice_number"] == "QU08904069"
    assert listed[0]["submitter_account"] == "未提供"
    assert detail is not None and detail["submitter_account"] == "未提供"
    assert db.rollbacks == 2
    assert "invoice_submitter_account_unavailable" in caplog.text
    assert "example.com" not in caplog.text
