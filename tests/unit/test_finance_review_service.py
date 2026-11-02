"""
模組名稱：tests.unit.test_finance_review_service
功能說明：財務審核（2-4）的逐欄確認、提醒確認、核准閘門、退回與駁回單元測試。
相依模組：app.services.finance_review_service、app.schemas.finance_review
注意事項：
    - 不連線資料庫；資料取自 fixtures/finance_review_case.json（依 C 的提交範例虛構）。
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.schemas.finance_review import (
    STATUS_APPROVED,
    STATUS_RETURNED,
    STATUS_SUBMITTED,
    STATUS_VOIDED,
    ReturnItem,
    ReviewCase,
)
from app.services.finance_review_service import (
    ALLOWED_TRANSITIONS,
    FinanceReviewError,
    account_category,
    acknowledge_warning,
    approval_blockers,
    approve,
    build_change_history,
    build_review_case,
    check_account_code,
    return_to_applicant,
    update_accounting_fields,
    warnings_from_validation_results,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "finance_review_case.json").read_text("utf-8")
)
REVIEWER = "55555555-5555-4555-8555-555555555555"
NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
ITEM_ID = "33333333-3333-4333-8333-333333333332"


def _case() -> ReviewCase:
    return build_review_case(FIXTURE["invoice"], FIXTURE["field_reviews"])


def _ready_case() -> ReviewCase:
    """提醒都已確認、會計欄位已填的審核資料；修改欄位不需逐欄確認。"""
    case = acknowledge_warning(
        _case(),
        "seller_name_unverified",
        "seller_name",
        REVIEWER,
        NOW,
        "已核對",
    )
    return update_accounting_fields(case, tax_type=None, deduction_code="1")


def _blocker_codes(error: pytest.ExceptionInfo[FinanceReviewError]) -> set[str]:
    return {blocker.code for blocker in error.value.blockers}


# ---- 組出審核資料 ----


def test_build_review_case_maps_v15_columns():
    case = _case()

    assert case.invoice_track == "AB"
    assert case.sales_amount == 6000
    assert len(case.field_reviews) == 4
    assert case.warnings[0].code == "seller_name_unverified"


def test_warnings_from_validation_results_skips_malformed_items():
    warnings = warnings_from_validation_results({"warnings": [{"field": "x"}, "bad"]})

    assert warnings == []


def test_warnings_from_validation_results_accepts_empty():
    assert warnings_from_validation_results(None) == []


# ---- 輪次與提醒 ----


def test_build_review_case_ignores_previous_rounds():
    old_round = dict(FIXTURE["field_reviews"][0], submission_round=0, field_name="seller_name")

    case = build_review_case(FIXTURE["invoice"], [old_round, *FIXTURE["field_reviews"]])

    assert "seller_name" not in {r.field_name for r in case.field_reviews}


def test_unchecked_field_reviews_do_not_block_approval():
    """2026-11-02 起核准前不再逐欄確認 AI 原值與送出值。"""
    case = _ready_case()
    assert any(not r.is_checked for r in case.field_reviews)

    assert approval_blockers(case, REVIEWER) == []


# ---- 修改軌跡 ----


def test_build_change_history_chains_values_per_field():
    trails = {t.field_name: t for t in build_change_history(FIXTURE["changes"])}

    assert trails["total_amount"].values == ("6030", "6330", "6300")
    assert trails["total_amount"].change_count == 2
    assert trails["unit_price"].item_id == ITEM_ID
    assert trails["deleted_item_3"].values[-1] is None


def test_build_change_history_keeps_first_change_order():
    names = [t.field_name for t in build_change_history(FIXTURE["changes"])]

    assert names == ["buyer_name", "total_amount", "unit_price", "deleted_item_3"]


def test_build_change_history_separates_rounds():
    second_round = dict(
        FIXTURE["changes"][1], submission_round=2, old_value="6300", new_value="6250"
    )

    trails = build_change_history([*FIXTURE["changes"], second_round])

    totals = [t for t in trails if t.field_name == "total_amount"]
    assert [t.submission_round for t in totals] == [1, 2]
    assert totals[1].values == ("6300", "6250")


def test_acknowledge_warning_requires_note():
    with pytest.raises(FinanceReviewError) as error:
        acknowledge_warning(_case(), "seller_name_unverified", "seller_name", REVIEWER, NOW, "  ")

    assert _blocker_codes(error) == {"NOTE_REQUIRED"}


def test_update_accounting_fields_rejects_unknown_codes():
    with pytest.raises(FinanceReviewError) as error:
        update_accounting_fields(_case(), tax_type="exempt", deduction_code="5")

    assert _blocker_codes(error) == {"BAD_TAX_TYPE", "BAD_DEDUCTION_CODE"}


# ---- 核准 ----


def test_approval_blockers_lists_all_open_items():
    codes = {b.code for b in approval_blockers(_case(), REVIEWER)}

    assert codes == {"WARNING_NOT_ACKNOWLEDGED", "DEDUCTION_CODE_MISSING"}


def test_approve_ready_case_returns_status_change():
    change = approve(_ready_case(), REVIEWER, NOW)

    assert change.old_status == "pending_department_approval"
    assert change.expected_status == "pending_department_approval"
    assert change.new_status == STATUS_APPROVED
    assert change.changed_by == REVIEWER


def test_approve_own_submission_rejected():
    case = _ready_case()

    with pytest.raises(FinanceReviewError) as error:
        approve(case, case.submitted_by, NOW)

    assert _blocker_codes(error) == {"SELF_APPROVAL"}


def test_approve_already_approved_rejected():
    case = replace(_ready_case(), status=STATUS_APPROVED)

    with pytest.raises(FinanceReviewError) as error:
        approve(case, REVIEWER, NOW)

    assert _blocker_codes(error) == {"NOT_REVIEWABLE"}


def test_resubmit_after_return_goes_back_to_submitted_status():
    """C 重送時沿用 pending_department_approval，狀態規則必須允許；退回後也可由申請人終止。"""
    assert STATUS_SUBMITTED in ALLOWED_TRANSITIONS[STATUS_RETURNED]
    assert STATUS_VOIDED in ALLOWED_TRANSITIONS[STATUS_RETURNED]


def test_finance_can_no_longer_reject():
    """2026-11-02 起取消財務駁回，財務只能核准或退回。"""
    assert ALLOWED_TRANSITIONS[STATUS_SUBMITTED] == {STATUS_APPROVED, STATUS_RETURNED}


# ---- 退回 ----


def test_return_to_applicant_requires_items():
    with pytest.raises(FinanceReviewError) as error:
        return_to_applicant(_case(), REVIEWER, [ReturnItem("total_amount", " ")], NOW)

    assert _blocker_codes(error) == {"RETURN_ITEMS_REQUIRED"}


def test_return_writes_field_review_comment_and_other_items_to_reason():
    items = [ReturnItem("total_amount", "請附折讓單"), ReturnItem("attachment", "補上採購單")]

    change = return_to_applicant(_case(), REVIEWER, items, NOW)

    assert change.new_status == STATUS_RETURNED
    assert len(change.return_items) == 2
    assert [(r.field_name, r.review_comment) for r in change.field_review_updates] == [
        ("total_amount", "請附折讓單")
    ]
    assert change.reason == "attachment：補上採購單"


def test_return_item_field_matches_by_item_id_and_unchecks():
    case = _case()

    change = return_to_applicant(
        case, REVIEWER, [ReturnItem("unit_price", "單價與原圖不符", ITEM_ID)], NOW
    )

    (update,) = change.field_review_updates
    assert (update.item_id, update.is_checked, update.checked_by) == (ITEM_ID, False, None)
    assert update.review_comment == "單價與原圖不符"
    assert change.reason is None


def test_return_item_field_without_item_id_goes_to_reason():
    """明細欄位沒帶 item_id 時無法對應，改寫入 reason，不會誤改其他明細。"""
    change = return_to_applicant(_case(), REVIEWER, [ReturnItem("unit_price", "請確認")], NOW)

    assert change.field_review_updates == ()
    assert change.reason == "unit_price：請確認"


def test_return_allowed_without_finishing_checks():
    """退回不需要先把所有欄位確認完。"""
    change = return_to_applicant(_case(), REVIEWER, [ReturnItem("buyer_name", "名稱不符")], NOW)

    assert change.new_status == STATUS_RETURNED


def test_returned_case_cannot_be_approved():
    case = replace(_ready_case(), status=STATUS_RETURNED)

    with pytest.raises(FinanceReviewError) as error:
        approve(case, REVIEWER, NOW)

    assert _blocker_codes(error) == {"NOT_REVIEWABLE"}


# ---- 費用大項與會計科目 ----


def test_account_category_maps_account_to_category():
    assert account_category("6211004") == "行政總務費用"
    assert account_category("6235090 管理-什費-其他") == "行政總務費用"
    assert account_category("1401090") == "固定資產"
    assert account_category("9999") is None


def test_check_account_code_allows_matching_category_and_blank():
    check_account_code("行政總務費用", "6211004")
    check_account_code("用人費用", "")
    check_account_code("", "")


def test_check_account_code_requires_category_first():
    with pytest.raises(FinanceReviewError) as error:
        check_account_code("", "6211004")

    assert _blocker_codes(error) == {"EXPENSE_CATEGORY_MISSING"}


def test_check_account_code_rejects_mismatch_and_unknown():
    with pytest.raises(FinanceReviewError) as mismatch:
        check_account_code("用人費用", "6211004")
    with pytest.raises(FinanceReviewError) as unknown:
        check_account_code("用人費用", "9999")
    with pytest.raises(FinanceReviewError) as bad_category:
        check_account_code("交際費用", "")

    assert _blocker_codes(mismatch) == {"ACCOUNT_CATEGORY_MISMATCH"}
    assert _blocker_codes(unknown) == {"BAD_ACCOUNT_CODE"}
    assert _blocker_codes(bad_category) == {"BAD_EXPENSE_CATEGORY"}


def test_deduction_code_is_independent_of_account():
    """扣抵代號不再與會計科目連動：費用科目也可以選固定資產或不得扣抵。"""
    check_account_code("行政總務費用", "6211004")
    case = update_accounting_fields(_case(), tax_type=None, deduction_code="4")

    assert case.deduction_code == "4"
