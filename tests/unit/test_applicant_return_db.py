"""測試目標：申請人端對資料庫案件的「退回補正（顯示）」與「終止」——讀寫資料庫，而不是記憶體示範資料。

退回補正的線上修改與重送尚未提供：資料庫案件只顯示退回項目與終止，不顯示舊的示範修改表單。
全部離線，替身 finance_db 服務；不連資料庫。
"""

from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.db.repositories import finance_review_repository as repo
from app.routers import invoices as inv
from app.schemas.finance_review import (
    STATUS_RETURNED,
    STATUS_SUBMITTED,
    STATUS_VOIDED,
    FieldReview,
    ReviewCase,
    StatusChange,
)
from app.services import finance_review_db_service as svc

INVOICE_ID = "5b0f8b0e-6c7a-4c55-9a1e-0a1b2c3d4e5f"
APPLICANT = "00000000-0000-4000-8000-0000000000a1"
FINANCE = "00000000-0000-4000-8000-0000000000f9"
NOW = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)
ORIGIN = {"Origin": "http://testserver"}
DEPARTMENT = "demo"  # 測試替身沒有選取部門時，路由使用的範圍值


def _case(status: str = STATUS_RETURNED) -> ReviewCase:
    return ReviewCase(
        invoice_id=INVOICE_ID,
        status=status,
        submission_round=1,
        submitted_by=APPLICANT,
        invoice_track="RT",
        invoice_number="39560800",
        seller_tax_id="43149447",
        sales_amount=None,
        tax_amount=None,
        total_amount=None,
        tax_type="taxable",
        deduction_code=None,
        field_reviews=(
            FieldReview("header", "total_amount", None, "100", "200", review_comment="請附折讓單"),
        ),
    )


class FakeDb:
    def __init__(self) -> None:
        self.case = _case()
        self.department_id: str | None = DEPARTMENT
        self.history = [
            StatusChange(
                INVOICE_ID,
                STATUS_SUBMITTED,
                STATUS_RETURNED,
                "attachment：補上採購單",
                FINANCE,
                NOW,
            )
        ]
        self.commits: list[tuple[StatusChange, bool]] = []
        self.error: Exception | None = None

    def load(self, invoice_id: str) -> svc.LoadedReview:
        return svc.LoadedReview(
            case=self.case, changes=[], history=self.history, department_id=self.department_id
        )

    def commit_change(
        self,
        change: StatusChange,
        *,
        case: ReviewCase,
        tax_type: str | None,
        record_reviewer: bool = True,
    ) -> None:
        if self.error:
            raise self.error
        self.commits.append((change, record_reviewer))


@pytest.fixture(autouse=True)
def _restore_state() -> Iterator[None]:
    store = deepcopy(inv._INVOICE_STORE)
    yield
    inv._INVOICE_STORE[:] = store
    inv._FINANCE_CASES.clear()
    inv._FINANCE_HISTORY.clear()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeDb:
    fake_db = FakeDb()
    monkeypatch.setattr(inv.finance_db, "load", fake_db.load)
    monkeypatch.setattr(inv.finance_db, "commit_change", fake_db.commit_change)
    invoice = dict(inv.DEMO_TEST_INVOICES[2])
    invoice.update(id=INVOICE_ID, uploaded_by=APPLICANT, stage=4)
    monkeypatch.setattr(inv.invoice_review_service, "fetch_invoice", lambda _id: invoice)
    return fake_db


def _as(user_id: str, admin_client: TestClient) -> TestClient:
    from app.core.deps import require_admin
    from app.main import app

    def dependency(request: Request) -> SimpleNamespace:
        admin = SimpleNamespace(id=user_id, display_name="申請人", role="admin", is_active=True)
        request.state.admin = admin
        return admin

    app.dependency_overrides[require_admin] = dependency
    return admin_client


@pytest.fixture
def client(admin_client: TestClient, fake: FakeDb) -> TestClient:
    return _as(APPLICANT, admin_client)


def _terminate(client: TestClient, reason: str = "供應商將重開發票") -> Any:
    return client.post(
        f"/invoices/{INVOICE_ID}/procurement/terminate", headers=ORIGIN, data={"reason": reason}
    )


def test_step3_shows_the_returned_items_from_the_database_without_the_demo_form(
    client: TestClient,
) -> None:
    html = client.get(f"/invoices/{INVOICE_ID}/flow?step=3").text

    assert "請附折讓單" in html and "補上採購單" in html  # 欄位退回原因與歷程中的附件項目
    assert "目前尚未提供線上修改被退回的案件" in html
    assert f"/invoices/{INVOICE_ID}/procurement/terminate" in html
    assert "procurement/resubmit" not in html  # 重送尚未提供
    assert "儲存補正內容" not in html and "flow-correct-form" not in html  # 舊的示範修改已停用


def test_step3_of_a_database_case_that_is_not_returned_has_no_demo_form(
    client: TestClient, fake: FakeDb
) -> None:
    fake.case = _case(STATUS_SUBMITTED)

    html = client.get(f"/invoices/{INVOICE_ID}/flow?step=3").text

    assert "此案件已由 API 建立，這裡不能修改欄位" in html
    assert "flow-correct-form" not in html and "procurement/terminate" not in html


def test_terminate_writes_voided_to_the_database_without_touching_the_reviewer(
    client: TestClient, fake: FakeDb
) -> None:
    response = _terminate(client)

    assert response.status_code == 303 and response.headers["location"] == "/invoices"
    change, record_reviewer = fake.commits[0]
    assert (change.old_status, change.new_status) == (STATUS_RETURNED, STATUS_VOIDED)
    assert (change.expected_status, change.changed_by, change.reason) == (
        STATUS_RETURNED,
        APPLICANT,
        "供應商將重開發票",
    )
    assert record_reviewer is False  # 終止不是財務審核，不改 reviewed_by／reviewed_at
    assert inv._FINANCE_HISTORY == {} and inv._FINANCE_CASES == {}  # 沒有寫進記憶體


def test_terminate_requires_a_reason_and_writes_nothing(client: TestClient, fake: FakeDb) -> None:
    response = _terminate(client, reason="  ")

    assert response.status_code == 400 and "終止時須填寫原因" in response.text
    assert fake.commits == []


def test_only_a_returned_case_can_be_terminated(client: TestClient, fake: FakeDb) -> None:
    fake.case = _case(STATUS_SUBMITTED)

    response = _terminate(client)

    assert response.status_code == 400 and "只有待補正的案件可以終止" in response.text
    assert fake.commits == []


def test_terminate_is_limited_to_the_current_department(client: TestClient, fake: FakeDb) -> None:
    fake.department_id = "another-department"

    response = _terminate(client)

    assert response.status_code == 400 and "這張請款單不屬於目前部門" in response.text
    assert fake.commits == []


def test_terminate_reports_a_lost_race_and_database_outage(
    client: TestClient, fake: FakeDb
) -> None:
    fake.error = svc.ReviewConflictError()
    assert "剛被其他人改變" in _terminate(client).text

    fake.error = svc.ReviewUnavailableError()
    assert "資料庫目前無法使用" in _terminate(client).text
    assert fake.commits == []


def test_terminate_needs_a_real_account(admin_client: TestClient, fake: FakeDb) -> None:
    response = _terminate(_as("applicant-1", admin_client))

    assert response.status_code in (400, 403)
    assert fake.commits == []


def test_database_writes_require_same_origin(client: TestClient, fake: FakeDb) -> None:
    accounting = {"tax_type": "taxable", "deduction_code": "1"}
    terminate = client.post(f"/invoices/{INVOICE_ID}/procurement/terminate", data={"reason": "x"})
    saved = client.post(
        f"/invoices/{INVOICE_ID}/finance-review/accounting", headers=ORIGIN, data=accounting
    )
    approve = client.post(f"/invoices/{INVOICE_ID}/finance-review/approve")

    assert terminate.status_code == 403
    assert saved.status_code in (303, 400)  # 帶同源標頭可通過（案件是待補正，會被規則擋下）
    assert approve.status_code in (400, 403)
    assert fake.commits == []


def test_applicant_terminate_does_not_overwrite_the_reviewer_in_sql() -> None:
    class Recording:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def execute(self, statement: Any, params: dict[str, Any]) -> Any:
            self.calls.append((str(statement), params))
            return SimpleNamespace(first=lambda: (INVOICE_ID,))

    db = Recording()
    change = StatusChange(INVOICE_ID, STATUS_RETURNED, STATUS_VOIDED, "x", APPLICANT, NOW)

    repo.apply_status_change(db, change, tax_type=None, submission_round=1, record_reviewer=False)  # type: ignore[arg-type]

    sql, params = db.calls[0]
    assert "CASE WHEN :record_reviewer THEN CAST(:reviewer AS uuid) ELSE reviewed_by END" in sql
    assert params["record_reviewer"] is False
