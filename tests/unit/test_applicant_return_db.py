"""測試目標：申請人端對資料庫案件的「退回補正（顯示）」與「終止」——讀寫資料庫，而不是記憶體示範資料。

資料庫案件的步驟 3 只顯示退回項目並連到補正頁（/invoices/{id}/correct，由 API 補正與終止），不顯示舊的示範修改表單。
舊的 POST /procurement/terminate 對資料庫案件改呼叫 API /void（不再直寫資料庫），其測試在 test_invoice_correction.py。
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


def test_step3_shows_the_returned_items_from_the_database_without_the_demo_form(
    client: TestClient,
) -> None:
    html = client.get(f"/invoices/{INVOICE_ID}/flow?step=3").text

    assert "請附折讓單" in html and "補上採購單" in html  # 欄位退回原因與歷程中的附件項目
    assert f'href="/invoices/{INVOICE_ID}/correct"' in html  # 補正與終止統一在補正頁（呼叫 API）
    assert (
        f"/invoices/{INVOICE_ID}/procurement/terminate" not in html
    )  # 步驟 3 不再提供直寫資料庫的終止表單
    assert "procurement/resubmit" not in html  # 重送尚未提供
    assert "儲存補正內容" not in html and "flow-correct-form" not in html  # 舊的示範修改已停用


def test_step3_of_a_database_case_that_is_not_returned_has_no_demo_form(
    client: TestClient, fake: FakeDb
) -> None:
    fake.case = _case(STATUS_SUBMITTED)

    html = client.get(f"/invoices/{INVOICE_ID}/flow?step=3").text

    assert "此案件已由 API 建立，這裡不能修改欄位" in html
    assert "flow-correct-form" not in html and "procurement/terminate" not in html


def test_database_writes_require_same_origin(client: TestClient, fake: FakeDb) -> None:
    accounting = {"tax_type": "taxable", "deduction_code": "1"}
    terminate = client.post(f"/invoices/{INVOICE_ID}/procurement/terminate", data={"reason": "x"})
    # 財務動作改用「非原提交者」的財務帳號，避免被本人限制（403）提前擋下而掩蓋同源檢查；
    # 財務審核各動作的同源檢查另在 test_finance_review_db 以待審核案件實測。
    finance = _as(FINANCE, client)
    saved = finance.post(
        f"/invoices/{INVOICE_ID}/finance-review/accounting", headers=ORIGIN, data=accounting
    )
    approve = finance.post(f"/invoices/{INVOICE_ID}/finance-review/approve")

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
