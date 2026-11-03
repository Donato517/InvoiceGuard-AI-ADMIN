"""測試目標：財務審核的核准、退回與課稅別更正對資料庫發票（UUID）寫入資料庫，而不是只改記憶體。

分三層：路由（替身 finance_db 服務）、服務（替身 Session，驗證交易）、Repository（替身 Session，驗證 SQL 順序與併發條件）。
全部離線；不連資料庫。示範發票（整數 ID）仍走記憶體，由 test_finance_review_pages 驗證。
"""

from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.db.repositories import finance_review_repository as repo
from app.routers import invoices as inv
from app.schemas.finance_review import (
    STATUS_APPROVED,
    STATUS_RETURNED,
    STATUS_SUBMITTED,
    FieldReview,
    ReturnItem,
    ReviewCase,
    StatusChange,
)
from app.services import finance_review_db_service as svc

INVOICE_ID = "5b0f8b0e-6c7a-4c55-9a1e-0a1b2c3d4e5f"
SUBMITTER = "00000000-0000-4000-8000-0000000000a1"
FINANCE = "00000000-0000-4000-8000-0000000000f9"
NOW = datetime(2026, 11, 3, 4, 0, tzinfo=UTC)


def _case(**changes: Any) -> ReviewCase:
    base: dict[str, Any] = {
        "invoice_id": INVOICE_ID,
        "status": STATUS_SUBMITTED,
        "submission_round": 1,
        "submitted_by": SUBMITTER,
        "invoice_track": "RT",
        "invoice_number": "39560800",
        "seller_tax_id": "43149447",
        "sales_amount": None,
        "tax_amount": None,
        "total_amount": None,
        "tax_type": "taxable",
        "deduction_code": None,
    }
    return ReviewCase(**(base | changes))


# ---- 路由 ----


class FakeDb:
    """替身 finance_db 服務：記錄寫入呼叫，並可模擬衝突與資料庫不可用。"""

    def __init__(self) -> None:
        self.case = _case()
        self.history: list[StatusChange] = []
        self.commits: list[tuple[StatusChange, ReviewCase, str | None]] = []
        self.tax_saves: list[tuple[str, str, str]] = []
        self.commit_error: Exception | None = None

    def load(self, invoice_id: str) -> svc.LoadedReview:
        return svc.LoadedReview(case=self.case, changes=[], history=self.history)

    def commit_change(
        self, change: StatusChange, *, case: ReviewCase, tax_type: str | None
    ) -> None:
        if self.commit_error:
            raise self.commit_error
        self.commits.append((change, case, tax_type))

    def save_tax_type(self, invoice_id: str, tax_type: str, expected_status: str) -> None:
        self.tax_saves.append((invoice_id, tax_type, expected_status))


@pytest.fixture(autouse=True)
def _restore_state() -> Iterator[None]:
    store = deepcopy(inv._INVOICE_STORE)
    yield
    inv._INVOICE_STORE[:] = store
    inv._ACCOUNTING_DRAFT.clear()
    inv._FINANCE_CASES.clear()
    inv._FINANCE_HISTORY.clear()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeDb:
    fake_db = FakeDb()
    monkeypatch.setattr(inv.finance_db, "load", fake_db.load)
    monkeypatch.setattr(inv.finance_db, "commit_change", fake_db.commit_change)
    monkeypatch.setattr(inv.finance_db, "save_tax_type", fake_db.save_tax_type)
    invoice = dict(inv.DEMO_TEST_INVOICES[2])
    invoice.update(id=INVOICE_ID, uploaded_by=SUBMITTER, stage=4)
    monkeypatch.setattr(inv.invoice_review_service, "fetch_invoice", lambda _id: invoice)
    return fake_db


def _as_finance(user_id: str, admin_client: TestClient) -> TestClient:
    from app.core.deps import require_admin
    from app.main import app

    def dependency(request: Request) -> SimpleNamespace:
        admin = SimpleNamespace(id=user_id, display_name="財務", role="admin", is_active=True)
        request.state.admin = admin
        return admin

    app.dependency_overrides[require_admin] = dependency
    return admin_client


@pytest.fixture
def client(admin_client: TestClient, fake: FakeDb) -> TestClient:
    return _as_finance(FINANCE, admin_client)


ACCOUNTING = {
    "tax_type": "taxable",
    "deduction_code": "1",
    "expense_category": "行政總務費用",
    "account_code": "6211004",
}


def test_step5_reads_the_database_case_and_history(client: TestClient, fake: FakeDb) -> None:
    fake.history = [
        StatusChange(INVOICE_ID, "", STATUS_SUBMITTED, "資料庫歷程備註", SUBMITTER, NOW)
    ]

    html = client.get(f"/invoices/{INVOICE_ID}/flow?step=5").text

    assert "待財務審核" in html and "資料庫歷程備註" in html


def test_approve_writes_to_the_database_with_the_accounting_note(
    client: TestClient, fake: FakeDb
) -> None:
    client.post(f"/invoices/{INVOICE_ID}/finance-review/accounting", data=ACCOUNTING)
    assert fake.tax_saves == []  # 課稅別與資料庫相同，不重複寫入

    response = client.post(f"/invoices/{INVOICE_ID}/finance-review/approve")

    assert response.status_code == 303
    change, case, tax_type = fake.commits[0]
    assert (change.new_status, change.expected_status, change.changed_by) == (
        STATUS_APPROVED,
        STATUS_SUBMITTED,
        FINANCE,
    )
    assert "扣抵代號：1 進貨及費用" in (change.reason or "")
    assert "會計科目：6211004" in (change.reason or "")
    assert (case.submission_round, tax_type) == (1, "taxable")
    assert inv._FINANCE_CASES == {}  # 沒有寫進記憶體
    assert inv._ACCOUNTING_DRAFT == {}  # 成功後清掉暫存


def test_changed_tax_type_is_saved_to_the_database(client: TestClient, fake: FakeDb) -> None:
    client.post(
        f"/invoices/{INVOICE_ID}/finance-review/accounting",
        data=ACCOUNTING | {"tax_type": "zero_rated"},
    )

    assert fake.tax_saves == [(INVOICE_ID, "zero_rated", STATUS_SUBMITTED)]


def test_approve_without_deduction_code_is_blocked_and_writes_nothing(
    client: TestClient, fake: FakeDb
) -> None:
    response = client.post(f"/invoices/{INVOICE_ID}/finance-review/approve")

    assert response.status_code == 400 and "請選擇扣抵代號" in response.text
    assert fake.commits == []


def test_self_approval_is_blocked_for_database_invoices(
    admin_client: TestClient, fake: FakeDb
) -> None:
    client = _as_finance(SUBMITTER, admin_client)
    client.post(f"/invoices/{INVOICE_ID}/finance-review/accounting", data=ACCOUNTING)

    response = client.post(f"/invoices/{INVOICE_ID}/finance-review/approve")

    assert response.status_code == 400 and "不能核准自己送出的請款單" in response.text
    assert fake.commits == []


def test_non_uuid_reviewer_cannot_write_review_records(
    admin_client: TestClient, fake: FakeDb
) -> None:
    client = _as_finance("finance-9", admin_client)

    response = client.post(f"/invoices/{INVOICE_ID}/finance-review/approve")

    assert response.status_code == 400 and "請以正式帳號登入" in response.text
    assert fake.commits == []


def test_return_writes_items_to_the_database(client: TestClient, fake: FakeDb) -> None:
    fake.case = _case(field_reviews=(FieldReview("header", "total_amount", None, "100", "200"),))

    response = client.post(
        f"/invoices/{INVOICE_ID}/finance-review/return",
        data={
            "return_field": ["total_amount", "attachment"],
            "return_reason": ["請附折讓單", "補上採購單"],
        },
    )

    assert response.status_code == 303
    change, _, _ = fake.commits[0]
    assert change.new_status == STATUS_RETURNED
    assert [(i.field, i.reason) for i in change.return_items] == [
        ("total_amount", "請附折讓單"),
        ("attachment", "補上採購單"),
    ]
    assert [u.field_name for u in change.field_review_updates] == ["total_amount"]
    assert inv._RETURN_SNAPSHOTS == {}


def test_conflict_is_reported_and_nothing_is_recorded(client: TestClient, fake: FakeDb) -> None:
    client.post(f"/invoices/{INVOICE_ID}/finance-review/accounting", data=ACCOUNTING)
    fake.commit_error = svc.ReviewConflictError()

    response = client.post(f"/invoices/{INVOICE_ID}/finance-review/approve")

    assert response.status_code == 400 and "剛被其他人改變" in response.text
    assert inv._ACCOUNTING_DRAFT  # 失敗時保留暫存，使用者可重試


def test_database_unavailable_is_reported_and_nothing_is_recorded(
    client: TestClient, fake: FakeDb
) -> None:
    client.post(f"/invoices/{INVOICE_ID}/finance-review/accounting", data=ACCOUNTING)
    fake.commit_error = svc.ReviewUnavailableError()

    response = client.post(f"/invoices/{INVOICE_ID}/finance-review/approve")

    assert response.status_code == 400 and "資料庫目前無法使用" in response.text


def test_memory_demo_invoices_still_use_the_memory_path(client: TestClient, fake: FakeDb) -> None:
    response = client.post("/invoices/3/finance-review/approve")

    assert response.status_code in (303, 400)
    assert fake.commits == []


# ---- 服務：交易 ----


class FakeSession:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.closed = False

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")

    def close(self) -> None:
        self.closed = True


def _change(**kwargs: Any) -> StatusChange:
    base: dict[str, Any] = {
        "invoice_id": INVOICE_ID,
        "old_status": STATUS_SUBMITTED,
        "new_status": STATUS_APPROVED,
        "reason": None,
        "changed_by": FINANCE,
        "changed_at": NOW,
    }
    return StatusChange(**(base | kwargs))


def test_commit_change_commits_when_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    monkeypatch.setattr(svc.repo, "apply_status_change", lambda db, change, **kw: True)

    svc.commit_change(_change(), case=_case(), tax_type=None, session_factory=lambda: session)

    assert session.events == ["commit"] and session.closed


def test_commit_change_rolls_back_and_raises_on_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    monkeypatch.setattr(svc.repo, "apply_status_change", lambda db, change, **kw: False)

    with pytest.raises(svc.ReviewConflictError):
        svc.commit_change(_change(), case=_case(), tax_type=None, session_factory=lambda: session)

    assert session.events == ["rollback"] and session.closed


def test_commit_change_rolls_back_on_database_error(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()

    def boom(db: Any, change: Any, **kw: Any) -> bool:
        raise svc.repo.SQLAlchemyError("x")

    monkeypatch.setattr(svc.repo, "apply_status_change", boom)

    with pytest.raises(svc.ReviewUnavailableError):
        svc.commit_change(_change(), case=_case(), tax_type=None, session_factory=lambda: session)

    assert session.events == ["rollback"]


@pytest.mark.parametrize("changes", [{"changed_by": "finance-9"}, {"invoice_id": "3"}])
def test_commit_change_refuses_non_uuid_ids_without_opening_a_session(
    changes: dict[str, str],
) -> None:
    def factory() -> Any:
        raise AssertionError("不應開啟 Session")

    with pytest.raises(svc.ReviewUnavailableError):
        svc.commit_change(_change(**changes), case=_case(), tax_type=None, session_factory=factory)


def test_unavailable_database_is_reported_by_load_and_save() -> None:
    def factory() -> Any:
        raise RuntimeError("no db")

    with pytest.raises(svc.ReviewUnavailableError):
        svc.load(INVOICE_ID, session_factory=factory)
    with pytest.raises(svc.ReviewUnavailableError):
        svc.save_tax_type(INVOICE_ID, "taxable", STATUS_SUBMITTED, session_factory=factory)


def test_accounting_note_lists_only_filled_parts() -> None:
    assert svc.accounting_note(
        deduction="1 進貨及費用", expense_category=None, account="6211004 x"
    ) == ("扣抵代號：1 進貨及費用；會計科目：6211004 x")
    assert svc.accounting_note(deduction=None, expense_category=None, account=None) is None


# ---- Repository：SQL 順序與併發條件 ----


class RecordingDb:
    """記錄 execute 的 SQL 文字與參數；第一個 UPDATE（狀態）可設定是否更新到列。"""

    def __init__(self, status_updated: bool) -> None:
        self.status_updated = status_updated
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement: Any, params: dict[str, Any]) -> Any:
        self.calls.append((str(statement), params))

        def first() -> Any:
            return (UUID(INVOICE_ID),) if self.status_updated else None

        return SimpleNamespace(first=first)


def test_status_update_is_conditional_on_the_expected_status() -> None:
    db = RecordingDb(status_updated=True)

    repo.apply_status_change(db, _change(), tax_type="taxable", submission_round=2)  # type: ignore[arg-type]

    sql, params = db.calls[0]
    assert "status = :expected_status" in sql and "RETURNING invoice_id" in sql
    assert params["expected_status"] == STATUS_SUBMITTED and params["new_status"] == STATUS_APPROVED


def test_a_lost_race_writes_no_history_and_no_field_reviews() -> None:
    db = RecordingDb(status_updated=False)
    change = _change(
        new_status=STATUS_RETURNED,
        return_items=(ReturnItem("total_amount", "x"),),
        field_review_updates=(FieldReview("header", "total_amount", None, "1", "2"),),
    )

    applied = repo.apply_status_change(db, change, tax_type=None, submission_round=1)  # type: ignore[arg-type]

    assert applied is False and len(db.calls) == 1


def test_applied_change_writes_history_then_field_review_comments() -> None:
    db = RecordingDb(status_updated=True)
    change = _change(
        new_status=STATUS_RETURNED,
        reason="attachment：補上採購單",
        field_review_updates=(
            FieldReview("header", "total_amount", None, "1", "2", review_comment="請附折讓單"),
        ),
    )

    applied = repo.apply_status_change(db, change, tax_type=None, submission_round=3)  # type: ignore[arg-type]

    assert applied is True
    names = [sql.split()[0] + " " + sql.split()[1] for sql, _ in db.calls]
    assert names == ["UPDATE public.invoices", "INSERT INTO", "UPDATE public.invoice_field_reviews"]
    assert db.calls[1][1]["reason"] == "attachment：補上採購單"
    review_params = db.calls[2][1]
    assert (review_params["submission_round"], review_params["comment"]) == (3, "請附折讓單")


def test_repository_never_commits_or_rolls_back() -> None:
    text = Path(repo.__file__).read_text(encoding="utf-8")

    assert ".commit(" not in text and ".rollback(" not in text
