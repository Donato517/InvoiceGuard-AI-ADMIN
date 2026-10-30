"""
模組名稱：tests.unit.test_procurement_case_service
功能說明：採購案件（2-5）的部門範圍列表、修改權限、退回項目解析與重送檢查單元測試。
相依模組：app.services.procurement_case_service、app.schemas.procurement_case
注意事項：
    - 不連線資料庫；資料取自 fixtures/procurement_cases.json（虛構資料）。
"""

import json
from datetime import date
from pathlib import Path

import pytest

from app.schemas.procurement_case import (
    ACTION_APPEALED,
    ACTION_CORRECTED,
    ATTACHMENT_FIELD,
    RESUBMIT_STATUS,
    STATUS_APPROVED,
    STATUS_RETURNED,
    CaseFilter,
    CaseScope,
    CorrectionResponse,
    ReturnedItem,
    ReturnRequest,
)
from app.services.procurement_case_service import (
    ProcurementCaseError,
    can_edit,
    latest_return,
    list_cases,
    prepare_resubmit,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "procurement_cases.json").read_text("utf-8")
)
INVOICES = FIXTURE["invoices"]
RETURNED = INVOICES[0]
SCOPE = CaseScope(user_id="33333333-3333-4333-8333-333333333333", department_id="dept-procurement")


ITEM_ID = "33333333-3333-4333-8333-333333333332"
CHANGED = {("unit_price", ITEM_ID), (ATTACHMENT_FIELD, None)}


def _return_request() -> ReturnRequest:
    request = latest_return(RETURNED["invoice_id"], FIXTURE["history"], FIXTURE["return_fields"])
    assert request is not None
    return request


def _complete_responses() -> list[CorrectionResponse]:
    return [
        CorrectionResponse("total_amount", ACTION_APPEALED, "供應商未給折讓，金額正確"),
        CorrectionResponse("unit_price", ACTION_CORRECTED, item_id=ITEM_ID),
        CorrectionResponse(ATTACHMENT_FIELD, ACTION_CORRECTED),
    ]


def _blocker_codes(error: pytest.ExceptionInfo[ProcurementCaseError]) -> set[str]:
    return {blocker.code for blocker in error.value.blockers}


# ---- 列表與修改權限 ----


def test_list_cases_only_selected_department():
    ids = {summary.invoice_id for summary in list_cases(INVOICES, SCOPE)}

    assert INVOICES[3]["invoice_id"] not in ids
    assert len(ids) == 3


def test_list_cases_returned_first_then_newest():
    summaries = list_cases(INVOICES, SCOPE)

    assert [s.status for s in summaries] == [
        STATUS_RETURNED,
        "pending_department_approval",
        STATUS_APPROVED,
    ]


def test_list_cases_labels_and_editable_flag():
    summaries = {s.invoice_id: s for s in list_cases(INVOICES, SCOPE)}

    returned = summaries[RETURNED["invoice_id"]]
    pending = summaries[INVOICES[1]["invoice_id"]]
    assert (returned.status_label, returned.is_editable) == ("待補正", True)
    assert (pending.status_label, pending.is_editable) == ("財務審核中", False)
    assert returned.invoice_no == "AB12345678"


def test_list_cases_filters_by_status_date_and_keyword():
    by_status = list_cases(INVOICES, SCOPE, CaseFilter(statuses=frozenset({STATUS_APPROVED})))
    by_date = list_cases(INVOICES, SCOPE, CaseFilter(date_from=date(2026, 10, 21)))
    by_keyword = list_cases(INVOICES, SCOPE, CaseFilter(keyword="cd00"))

    assert [s.invoice_no for s in by_status] == ["CD00000001"]
    assert [s.invoice_no for s in by_date] == ["AB12345679"]
    assert [s.invoice_no for s in by_keyword] == ["CD00000001"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("draft", True),
        (STATUS_RETURNED, True),
        ("pending_department_approval", False),
        ("pending_accounting_review", False),
        (STATUS_APPROVED, False),
        ("rejected", False),
    ],
)
def test_can_edit_only_draft_and_returned(status, expected):
    assert can_edit(status) is expected


# ---- 退回項目 ----


def test_latest_return_reads_field_comments_then_reason():
    request = _return_request()

    assert request.items == (
        ReturnedItem("total_amount", "請附折讓單"),
        ReturnedItem("unit_price", "單價與原圖不符", ITEM_ID),
        ReturnedItem(ATTACHMENT_FIELD, "補上採購單"),
    )
    assert request.returned_by == "55555555-5555-4555-8555-555555555555"


def test_latest_return_skips_unchecked_field_without_comment():
    """財務沒確認但也沒寫原因的欄位，不列為要補正的項目。"""
    fields = {item.field for item in _return_request().items}

    assert "buyer_name" not in fields


def test_latest_return_uses_most_recent_return_reason():
    second = dict(
        FIXTURE["history"][1],
        reason="seller_name：名稱不符",
        changed_at="2026-10-25T09:00:00+08:00",
    )

    request = latest_return(RETURNED["invoice_id"], [*FIXTURE["history"], second], [])

    assert request.items == (ReturnedItem("seller_name", "名稱不符"),)


def test_latest_return_line_without_field_goes_to_other():
    row = dict(FIXTURE["history"][1], reason="請重新拍攝")

    request = latest_return(RETURNED["invoice_id"], [row], [])

    assert request.items == (ReturnedItem("其他", "請重新拍攝"),)


def test_latest_return_none_when_never_returned():
    assert latest_return("x", FIXTURE["history"][:1], FIXTURE["return_fields"]) is None


# ---- 重送檢查 ----


def test_prepare_resubmit_returns_plan():
    plan = prepare_resubmit(RETURNED, SCOPE, _return_request(), _complete_responses(), CHANGED)

    assert plan.expected_status == STATUS_RETURNED
    assert plan.target_status == RESUBMIT_STATUS
    assert plan.next_round == 2
    assert plan.reason == (
        "total_amount：申覆（不修改）－供應商未給折讓，金額正確\n"
        "unit_price：已補正\n"
        "attachment：已補正"
    )


def test_prepare_resubmit_requires_every_item_answered():
    responses = _complete_responses()[:2]

    with pytest.raises(ProcurementCaseError) as error:
        prepare_resubmit(RETURNED, SCOPE, _return_request(), responses, CHANGED)

    assert _blocker_codes(error) == {"ITEM_NOT_ANSWERED"}


def test_prepare_resubmit_item_field_matched_by_item_id():
    """明細欄位回覆時沒帶 item_id，視為沒回覆該明細。"""
    responses = [
        _complete_responses()[0],
        CorrectionResponse("unit_price", ACTION_CORRECTED),
        _complete_responses()[2],
    ]

    with pytest.raises(ProcurementCaseError) as error:
        prepare_resubmit(RETURNED, SCOPE, _return_request(), responses, CHANGED)

    assert _blocker_codes(error) == {"ITEM_NOT_ANSWERED", "UNKNOWN_ITEM", "ITEM_NOT_CHANGED"}


def test_prepare_resubmit_corrected_item_must_actually_change():
    """對應退件情境 92：只回覆「已修改」，實際欄位未變更。"""
    with pytest.raises(ProcurementCaseError) as error:
        prepare_resubmit(
            RETURNED, SCOPE, _return_request(), _complete_responses(), {(ATTACHMENT_FIELD, None)}
        )

    assert _blocker_codes(error) == {"ITEM_NOT_CHANGED"}


def test_prepare_resubmit_appeal_requires_note():
    responses = [
        CorrectionResponse("total_amount", ACTION_APPEALED, " "),
        *_complete_responses()[1:],
    ]

    with pytest.raises(ProcurementCaseError) as error:
        prepare_resubmit(RETURNED, SCOPE, _return_request(), responses, CHANGED)

    assert _blocker_codes(error) == {"APPEAL_NOTE_REQUIRED"}


def test_prepare_resubmit_rejects_unknown_item_and_action():
    responses = [*_complete_responses(), CorrectionResponse("seller_name", "ignored")]

    with pytest.raises(ProcurementCaseError) as error:
        prepare_resubmit(RETURNED, SCOPE, _return_request(), responses, CHANGED)

    assert _blocker_codes(error) == {"UNKNOWN_ITEM", "BAD_ACTION"}


def test_prepare_resubmit_only_returned_case():
    with pytest.raises(ProcurementCaseError) as error:
        prepare_resubmit(INVOICES[1], SCOPE, _return_request(), _complete_responses(), CHANGED)

    assert _blocker_codes(error) == {"NOT_RETURNED"}


def test_prepare_resubmit_other_department_rejected():
    other = CaseScope(user_id=SCOPE.user_id, department_id="dept-administration")

    with pytest.raises(ProcurementCaseError) as error:
        prepare_resubmit(RETURNED, other, _return_request(), _complete_responses(), CHANGED)

    assert _blocker_codes(error) == {"OUT_OF_SCOPE"}
