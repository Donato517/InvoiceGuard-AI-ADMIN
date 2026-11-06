"""
模組名稱：tests.unit.test_f09_snapshot_approval
功能說明：F09 快照只認正式財務核准；資料庫待審案件的檢視狀態是 passed，不得被當成已核准。
相依模組：app.routers.invoices、app.db.repositories.invoice_repository、app.services.format21_service
注意事項：
    - 使用真實的 _row_to_invoice_dict、_to_filing_snapshot、validate_snapshot、check_snapshot、build_format21_file；
      只有「發票清單來源」以替身取代。離線測試，不連資料庫，不代表正式環境驗收。
    - 全部使用虛構資料（統編 27601907、04595252 同 test_pages 的批次申報案例）。
"""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db.repositories.invoice_repository import _row_to_invoice_dict
from app.routers import invoices as invoices_router
from app.services.company_profile_service import HardcodedCompanyProfileProvider
from app.services.format21_service import build_format21_file, check_snapshot, validate_snapshot

PERIOD = "11510"
NOT_APPROVED = "NOT_APPROVED"
DB_STATUSES = (
    "draft",
    "pending_unit_approval",
    "pending_accounting_review",
    "returned_to_applicant",
    "returned_to_accounting",
    "rejected",
    "voided",
    "approved",
)


_Profile = HardcodedCompanyProfileProvider  # 虛構公司資料：統編 04595252、稅籍 123456789
PROFILE = _Profile().get_profile()


def _row(status: str, number: str = "08904069", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "invoice_id": f"inv-{status}-{number}",  # 固定字串 ID，結果不受 hash 隨機化影響
        "status": status,
        "invoice_track": "EC",
        "invoice_number": number,
        "invoice_date": date(2026, 9, 15),
        "seller_tax_id": "27601907",
        "buyer_tax_id": "04595252",
        "sales_amount": 6000,
        "tax_amount": 300,
        "total_amount": 6300,
        "tax_type": "taxable",
    }
    row.update(overrides)
    return row


def _mapped(status: str, number: str = "08904069", **overrides: Any) -> dict[str, Any]:
    """真實映射：資料庫列 → ADMIN 發票字典。"""
    return _row_to_invoice_dict(_row(status, number, **overrides))


def _codes(invoice: dict[str, Any]) -> list[str]:
    snapshot = invoices_router._to_filing_snapshot(invoice)
    return [i.code for i in validate_snapshot(snapshot, PROFILE)]


# ---- 1. 待審資料庫案件：檢視狀態是 passed，但不是正式核准 ----
@pytest.mark.parametrize("status", ["pending_unit_approval", "pending_accounting_review"])
def test_pending_db_invoice_is_not_approved_for_f09(status: str) -> None:
    mapped = _mapped(status)
    # 前提：確實是「檢視通過、審核待處理」這個會被誤判的組合
    assert mapped["status"] == "passed" and mapped["review_status"] == "pending"
    snapshot = invoices_router._to_filing_snapshot(mapped)
    assert snapshot["status"] != "approved"
    assert NOT_APPROVED in _codes(mapped)


# ---- 2. 正式核准：不得 NOT_APPROVED，且其他檢核仍會執行並通過 ----
def test_approved_db_invoice_passes_all_f09_checks() -> None:
    mapped = _mapped("approved")
    snapshot = invoices_router._to_filing_snapshot(mapped)
    assert snapshot["status"] == "approved"
    record, issues = check_snapshot(snapshot, PROFILE, PERIOD)
    assert issues == []
    assert record.track + record.number == "EC08904069"


# ---- 3、4. 退回、駁回、作廢、草稿，以及補正重送後的待審：一律不產檔 ----
@pytest.mark.parametrize(
    "status",
    [s for s in DB_STATUSES if s != "approved"],
)
def test_every_non_approved_db_status_blocks_f09_export(status: str) -> None:
    mapped = _mapped(status)
    assert NOT_APPROVED in _codes(mapped)
    result = build_format21_file([invoices_router._to_filing_snapshot(mapped)], _Profile(), PERIOD)
    assert result.content is None and result.filename is None


def test_resubmitted_after_correction_stays_blocked_until_reapproved() -> None:
    """補正重送：returned_to_applicant → pending_*（待審，檢視狀態 passed）→ 重新核准才可產檔。"""
    returned = _mapped("returned_to_applicant")
    resubmitted = _mapped("pending_accounting_review")
    reapproved = _mapped("approved")
    assert NOT_APPROVED in _codes(returned)
    assert NOT_APPROVED in _codes(resubmitted)
    assert NOT_APPROVED not in _codes(reapproved)


# ---- 5. 核准與待審混選：使用真實 build_format21_file，整批不產檔 ----
def test_mixed_approved_and_pending_batch_produces_no_file() -> None:
    approved = _mapped("approved", number="08904069")
    pending = _mapped("pending_accounting_review", number="08904070")
    result = build_format21_file(
        [invoices_router._to_filing_snapshot(i) for i in (approved, pending)], _Profile(), PERIOD
    )
    assert result.content is None and result.filename is None
    assert list(result.issues) == [pending["id"]]  # 問題只出在待審那筆
    assert [i.code for i in result.issues[pending["id"]]] == [NOT_APPROVED]


def test_only_approved_batch_produces_a_file() -> None:
    approved = [_mapped("approved", number="08904069"), _mapped("approved", number="08904070")]
    result = build_format21_file(
        [invoices_router._to_filing_snapshot(i) for i in approved], _Profile(), PERIOD
    )
    assert result.content is not None and result.filename == "04595252.TXT"
    assert len([ln for ln in result.content.split("\r\n") if ln]) == 2


# ---- 6. 五筆現有示範案件：行為不變 ----
def test_five_demo_invoices_keep_their_f09_approval_state() -> None:
    expected = {"1": True, "2": True, "3": False, "4": False, "5": True}
    demo = {str(i["id"]): i for i in invoices_router.DEMO_TEST_INVOICES}
    assert set(expected) <= set(demo)
    for invoice_id, approved in expected.items():
        snapshot = invoices_router._to_filing_snapshot(demo[invoice_id])
        assert (snapshot["status"] == "approved") is approved, invoice_id
        assert (NOT_APPROVED not in _codes(demo[invoice_id])) is approved, invoice_id


def test_demo_invoice_with_passed_view_status_but_pending_review_is_not_approved() -> None:
    """示範案件若只有檢視狀態 passed、審核狀態不是 approved，同樣不得視為核准。"""
    invoice = {
        "id": 9001,
        "invoice_number": "EC08904069",
        "invoice_date": "115/09/15",
        "seller_tax_id": "27601907",
        "buyer_tax_id": "04595252",
        "net_amount": 6000,
        "tax_amount": 300,
        "tax_category": "應稅",
        "deduction_code": "1 - 進貨及費用",
        "status": "passed",
        "review_status": "pending",
    }
    assert NOT_APPROVED in _codes(invoice)


# ---- 7. 已核准但其他資料檢核失敗：仍回報 F09 錯誤，且不是 NOT_APPROVED ----
def test_approved_but_other_f09_checks_still_fail() -> None:
    wrong_buyer = _mapped("approved", buyer_tax_id="12345678")
    snapshot = invoices_router._to_filing_snapshot(wrong_buyer)
    _, issues = check_snapshot(snapshot, PROFILE, PERIOD)
    codes = [i.code for i in issues]
    assert "BUYER_BAN_MISMATCH" in codes and NOT_APPROVED not in codes
    result = build_format21_file([snapshot], _Profile(), PERIOD)
    assert result.content is None


# ---- 8. 批次申報清單：待審資料庫案件不得符合產檔資格 ----
@pytest.fixture
def batch_source(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    invoices: list[dict[str, Any]] = []
    monkeypatch.setattr(invoices_router, "_get_active_invoices", lambda request=None: invoices)
    monkeypatch.setattr(invoices_router, "_FILING_PROFILE", _Profile())
    return invoices


def _rows(batch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in batch["rows"]}


def test_batch_list_marks_pending_db_invoice_as_not_exportable(
    batch_source: list[dict[str, Any]],
) -> None:
    approved = _mapped("approved", number="08904069")
    pending = _mapped("pending_accounting_review", number="08904070")
    batch_source.extend([approved, pending])
    batch = invoices_router._build_batch_filing_context(PERIOD, "2026-09-01", "2026-10-31")
    rows = _rows(batch)
    assert rows[approved["id"]]["checked"] is True
    assert rows[pending["id"]]["checked"] is False
    assert [i["code"] for i in rows[pending["id"]]["issues"]] == [NOT_APPROVED]
    # 件數與合計只計入已核准且通過檢核的案件
    assert (batch["count"], batch["sales_amount"], batch["tax_amount"]) == (1, 6000, 300)


def test_batch_page_shows_not_approved_reason_for_pending_db_invoice(
    admin_client: TestClient, batch_source: list[dict[str, Any]]
) -> None:
    batch_source.append(_mapped("pending_accounting_review", number="08904070"))
    page = admin_client.get(
        "/invoices/filing",
        params={"period": PERIOD, "from_date": "2026-09-01", "to_date": "2026-10-31"},
    )
    assert page.status_code == 200
    assert "EC08904070" in page.text and "請款單未核准，不可匯出" in page.text


def test_batch_export_route_refuses_pending_db_invoice_for_other_users(
    admin_client: TestClient, batch_source: list[dict[str, Any]]
) -> None:
    """非本人（登入者與上傳者不同）也受相同核准門檻限制；被拒絕時不產生任何檔案。"""
    pending = _mapped("pending_accounting_review", number="08904070")
    batch_source.append(pending)
    response = admin_client.post(
        "/invoices/filing/export", data={"period": PERIOD, "invoice_ids": [pending["id"]]}
    )
    assert response.status_code == 400
    assert "content-disposition" not in response.headers
    assert "請款單未核准，不可匯出" in response.text


def test_batch_export_route_exports_approved_db_invoice(
    admin_client: TestClient, batch_source: list[dict[str, Any]]
) -> None:
    approved = _mapped("approved", number="08904069")
    batch_source.append(approved)
    response = admin_client.post(
        "/invoices/filing/export", data={"period": PERIOD, "invoice_ids": [approved["id"]]}
    )
    assert response.status_code == 200
    assert "04595252.TXT" in response.headers["content-disposition"]


# ---- 單張檢核（步驟 6）：同樣不再為待審案件產生預覽 ----
def test_single_filing_check_has_no_preview_for_pending_db_invoice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(invoices_router, "_FILING_PROFILE", _Profile())
    pending = invoices_router._single_filing_check(_mapped("pending_accounting_review"), PERIOD)
    approved = invoices_router._single_filing_check(_mapped("approved"), PERIOD)
    assert [i["code"] for i in pending["issues"]] == [NOT_APPROVED]
    assert pending["preview_line"] is None
    assert approved["issues"] == [] and len(approved["preview_line"]) == 81
