"""測試目標：資料庫發票轉成 ADMIN 字典時，缺值保持缺值，不虛構身分、課稅別或金額。

背景（PR #21 第二輪審查 Batch 2）：NULL 的 uploaded_by 被填成 "1"（示範身分）、NULL 的課稅別被推定為
應稅、NULL 的金額被 int(... or 0) 變成 0，F09 因此看不到缺漏。核准規則（review_status == "approved"）不變。
全部離線，不連資料庫。
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.db.repositories.invoice_repository import _row_to_invoice_dict
from app.routers import invoices as invoices_router
from app.services.company_profile_service import HardcodedCompanyProfileProvider
from app.services.format21_service import check_snapshot

PROFILE = HardcodedCompanyProfileProvider().get_profile()
PERIOD = "11510"


def _row(status: str = "approved", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "invoice_id": "inv-1",
        "status": status,
        "invoice_track": "EC",
        "invoice_number": "08904069",
        "invoice_date": date(2026, 9, 15),
        "seller_tax_id": "27601907",
        "buyer_tax_id": "04595252",
        "sales_amount": 6000,
        "tax_amount": 300,
        "total_amount": 6300,
        "tax_type": "taxable",
        "submitted_by": "6a4c1b8e-0000-4000-8000-000000000001",
    }
    row.update(overrides)
    return row


def _codes(row: dict[str, Any]) -> list[str]:
    snapshot = invoices_router._to_filing_snapshot(_row_to_invoice_dict(row))
    snapshot["deduction_code"] = "1"  # 扣抵代號另案處理（見 REVIEW_FIX_LOG），這裡只看金額與課稅別
    return [i.code for i in check_snapshot(snapshot, PROFILE, PERIOD)[1]]


# ---- 身分 ----


def test_missing_submitter_is_not_replaced_by_the_demo_user() -> None:
    assert _row_to_invoice_dict(_row(submitted_by=None))["uploaded_by"] == ""


def test_real_submitter_is_kept() -> None:
    assert _row_to_invoice_dict(_row())["uploaded_by"] == "6a4c1b8e-0000-4000-8000-000000000001"


def test_missing_submitter_does_not_match_the_demo_user_for_access() -> None:
    """缺值的提交者不能被當成示範使用者 "1" 而放行非財務使用者。"""
    invoice = _row_to_invoice_dict(_row(submitted_by=None))
    assert str(invoice["uploaded_by"]) != "1"


# ---- 課稅別 ----


@pytest.mark.parametrize(
    ("raw", "category"),
    [("taxable", "應稅"), ("zero_rated", "零稅率"), ("tax_exempt", "免稅")],
)
def test_known_tax_types_still_map(raw: str, category: str) -> None:
    assert _row_to_invoice_dict(_row(tax_type=raw))["tax_category"] == category


@pytest.mark.parametrize("raw", [None, ""])
def test_missing_tax_type_is_not_assumed_taxable(raw: str | None) -> None:
    invoice = _row_to_invoice_dict(_row(tax_type=raw))
    assert invoice["tax_category"] == "" and invoice["tax_type"] == ""
    assert "BAD_TAX_TYPE" in _codes(_row(tax_type=raw))


# ---- 金額：NULL ≠ 0 ----


def test_null_amounts_stay_none_for_filing_and_are_reported() -> None:
    row = _row(sales_amount=None, tax_amount=None)
    invoice = _row_to_invoice_dict(row)
    assert invoice["sales_amount_db"] is None and invoice["tax_amount_db"] is None
    snapshot = invoices_router._to_filing_snapshot(invoice)
    assert snapshot["sales_amount"] is None and snapshot["tax_amount"] is None
    assert "BAD_AMOUNT" in _codes(row)


def test_real_zero_amount_stays_zero_and_is_not_missing() -> None:
    row = _row(sales_amount=0, tax_amount=0, total_amount=0, tax_type="tax_exempt")
    invoice = _row_to_invoice_dict(row)
    assert invoice["sales_amount_db"] == 0 and invoice["tax_amount_db"] == 0
    assert _codes(row) == []


def test_decimal_amount_is_not_silently_truncated_for_filing() -> None:
    row = _row(sales_amount=Decimal("6000.50"), tax_amount=Decimal("300"))
    snapshot = invoices_router._to_filing_snapshot(_row_to_invoice_dict(row))
    assert snapshot["sales_amount"] == Decimal("6000.50")  # 不是 6000
    assert "BAD_AMOUNT" in _codes(row)  # F09 要求整數，所以被擋下而不是悄悄改值


def test_display_amounts_are_unchanged() -> None:
    invoice = _row_to_invoice_dict(_row())
    assert invoice["net_amount"] == 6000 and invoice["tax_amount"] == 300
    assert invoice["total_amount"] == 6300


# ---- 核准規則不變 ----


@pytest.mark.parametrize("status", ["pending_accounting_review", "returned_to_applicant", "draft"])
def test_unapproved_status_is_still_blocked(status: str) -> None:
    assert "NOT_APPROVED" in _codes(_row(status))


def test_complete_approved_invoice_still_passes() -> None:
    assert _codes(_row("approved")) == []


def test_demo_invoice_filing_snapshot_is_unchanged() -> None:
    """示範案件沒有 *_db 欄位，沿用顯示金額。"""
    demo = dict(invoices_router.DEMO_TEST_INVOICES[0])
    snapshot = invoices_router._to_filing_snapshot(demo)
    assert snapshot["sales_amount"] == demo.get("net_amount")
    assert snapshot["tax_amount"] == demo.get("tax_amount")
