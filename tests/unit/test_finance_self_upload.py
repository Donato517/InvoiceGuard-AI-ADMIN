"""
模組名稱：tests.unit.test_finance_self_upload
功能說明：財務審核「本人上傳／無權審核」與上傳者帳號顯示（方案 1）。
相依模組：app.routers.invoices、app.utils.account_display
注意事項：
    - 只用合成帳號與示範資料，不連資料庫；財務人員 ID 使用合成 UUID。
    - 禁止本人退回已於 2026-11-05 核定，後端以 SELF_RETURN 拒絕。
"""

from collections.abc import Iterator
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.routers import invoices as invoices_router
from app.utils.account_display import account_display, is_same_user

_ORIGIN = {"Origin": "http://testserver"}
ME = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(autouse=True)
def _restore_demo_state() -> Iterator[None]:
    store = deepcopy(invoices_router._INVOICE_STORE)
    yield
    invoices_router._INVOICE_STORE[:] = store
    invoices_router._FINANCE_CASES.clear()
    invoices_router._FINANCE_HISTORY.clear()
    invoices_router._RETURN_SNAPSHOTS.clear()


@pytest.fixture
def finance_me(admin_client: TestClient) -> TestClient:
    from app.core.deps import require_admin
    from app.main import app

    def as_me(request: Request) -> SimpleNamespace:
        admin = SimpleNamespace(id=ME, display_name="財務甲", role="admin", is_active=True)
        request.state.admin = admin
        return admin

    app.dependency_overrides[require_admin] = as_me
    return admin_client


def _set_uploader(invoice_id: int, uploader: str, account: str | None) -> None:
    inv = next(i for i in invoices_router._INVOICE_STORE if str(i["id"]) == str(invoice_id))
    inv["uploaded_by"] = uploader
    inv["submitter_account"] = account_display(account)


# D 帳號顯示
def test_account_display_rules() -> None:
    assert account_display("rayneli2524@example.com") == "rayneli2524"
    assert account_display("plain_account") == "plain_account"
    assert account_display(None) == "未提供"
    assert account_display("  ") == "未提供"
    assert account_display("@example.com") == "@example.com"


def test_same_user_compares_ids_not_display_text() -> None:
    assert is_same_user(ME.upper(), ME)
    assert not is_same_user(OTHER, ME)
    assert not is_same_user(None, ME)
    assert not is_same_user("", "")


# A 本人上傳本人查看
def test_list_marks_own_upload(finance_me: TestClient) -> None:
    _set_uploader(3, ME, "me@example.com")
    html = finance_me.get("/invoices").text
    assert "上傳者帳號" in html
    assert "data-self-upload-badge" in html
    assert "本人上傳／無權審核" in html
    assert "檢視詳情" in html
    assert ">me<" in html and "@example.com" not in html.split("<tbody>")[1]


def test_self_upload_cannot_view_review_step(finance_me: TestClient) -> None:
    """2026-11-06 使用者確認：本人上傳者不得查看步驟 5（取代先前「可檢視、不可操作」的設計）。"""
    _set_uploader(3, ME, "me@example.com")
    response = finance_me.get("/invoices/3/flow?step=5")
    assert response.status_code == 403
    assert "核對欄位（14 項）" not in response.text
    assert 'action="/invoices/3/finance-review/approve"' not in response.text


def test_self_approve_post_is_rejected_by_server(finance_me: TestClient) -> None:
    _set_uploader(3, ME, "me@example.com")
    response = finance_me.post("/invoices/3/finance-review/approve")
    assert response.status_code == 403
    assert "不得由您自行核准、退回或儲存會計欄位" in response.text
    case = invoices_router._finance_case(invoices_router._find_invoice("3"))
    assert case.status != "approved"
    assert not invoices_router._FINANCE_HISTORY.get("3")


# B 他人上傳
def test_other_upload_keeps_normal_review(finance_me: TestClient) -> None:
    _set_uploader(3, OTHER, "other@example.com")
    list_html = finance_me.get("/invoices").text
    assert "data-self-upload-badge" not in list_html
    detail = finance_me.get("/invoices/3/flow?step=5").text
    assert "data-self-upload-notice" not in detail
    assert "確認核准" in detail
    assert "退回修正（逐項填寫）" in detail


# E 回歸：搜尋與篩選仍包含本人案件
def test_self_upload_stays_in_list_and_filters(finance_me: TestClient) -> None:
    _set_uploader(3, ME, None)
    number = next(i for i in invoices_router._INVOICE_STORE if str(i["id"]) == "3")[
        "invoice_number"
    ]
    html = finance_me.get(f"/invoices?q={number}").text
    assert number in html
    assert "未提供" in html


# C 繞過前端：退回（2026-11-05 團隊核定禁止本人退回）
def test_self_return_post_is_rejected_by_server(finance_me: TestClient) -> None:
    _set_uploader(3, ME, "me@example.com")
    response = finance_me.post(
        "/invoices/3/finance-review/return",
        data={"return_field": "total_amount", "return_reason": "請附折讓單"},
    )
    assert response.status_code == 403
    case = invoices_router._finance_case(invoices_router._find_invoice("3"))
    assert case.status not in {"returned_to_applicant"}
    assert not invoices_router._FINANCE_HISTORY.get("3")


def test_other_can_still_return(finance_me: TestClient) -> None:
    _set_uploader(3, OTHER, "other@example.com")
    response = finance_me.post(
        "/invoices/3/finance-review/return",
        data={"return_field": "total_amount", "return_reason": "請附折讓單"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert invoices_router._FINANCE_HISTORY.get("3")


def test_self_accounting_post_is_rejected_and_writes_nothing(finance_me: TestClient) -> None:
    _set_uploader(3, ME, "me@example.com")
    before = deepcopy(invoices_router._find_invoice("3"))
    response = finance_me.post(
        "/invoices/3/finance-review/accounting",
        data={"tax_type": "zero_rated", "deduction_code": "2"},
    )
    assert response.status_code == 403
    after = invoices_router._find_invoice("3")
    for key in ("tax_category", "tax_type", "deduction_code", "account_code", "expense_category"):
        assert after.get(key) == before.get(key)
    assert not invoices_router._FINANCE_HISTORY.get("3")


def test_other_finance_can_save_accounting(finance_me: TestClient) -> None:
    _set_uploader(3, OTHER, "other@example.com")
    response = finance_me.post(
        "/invoices/3/finance-review/accounting",
        data={"tax_type": "taxable", "deduction_code": "1"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_forged_submitted_by_in_request_is_ignored(finance_me: TestClient) -> None:
    _set_uploader(3, ME, "me@example.com")
    response = finance_me.post("/invoices/3/finance-review/approve", data={"submitted_by": OTHER})
    assert response.status_code == 403


def test_non_finance_cannot_operate(admin_client: TestClient) -> None:
    from app.core.deps import require_admin
    from app.main import app

    _set_uploader(3, ME, "me@example.com")
    original = invoices_router._INVOICE_STORE
    try:
        app.dependency_overrides.pop(require_admin, None)
        for action in ("approve", "return", "accounting"):
            response = admin_client.post(f"/invoices/3/finance-review/{action}")
            assert response.status_code in {401, 403, 303, 307}
    finally:
        assert original is invoices_router._INVOICE_STORE


# 舊版審核入口 POST /{id}/review 與步驟 5 使用同一個本人限制
def test_legacy_review_endpoint_denies_self_upload_and_keeps_state(
    finance_me: TestClient,
) -> None:
    _set_uploader(3, ME, "me@example.com")
    before = deepcopy(invoices_router._find_invoice("3"))
    response = finance_me.post(
        "/invoices/3/review", headers=_ORIGIN, data={"decision": "passed", "tax_type": "免稅"}
    )
    assert response.status_code == 403
    after = invoices_router._find_invoice("3")
    assert (after["review_status"], after["status"], after["tax_type"]) == (
        before["review_status"],
        before["status"],
        before["tax_type"],
    )


def test_legacy_review_endpoint_still_works_for_other_reviewer(finance_me: TestClient) -> None:
    _set_uploader(3, OTHER, "other@example.com")
    response = finance_me.post(
        "/invoices/3/review", headers=_ORIGIN, data={"decision": "passed"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert invoices_router._find_invoice("3")["review_status"] == "approved"


def test_legacy_detail_page_hides_review_form_for_self_upload(finance_me: TestClient) -> None:
    _set_uploader(3, ME, "me@example.com")
    html = finance_me.get("/invoices/3").text
    assert "data-self-upload-notice" in html
    assert 'action="/invoices/3/review"' not in html
    _set_uploader(3, OTHER, "other@example.com")
    assert 'action="/invoices/3/review"' in finance_me.get("/invoices/3").text


# 舊入口的其餘安全檢核：同源、狀態、decision、欄位內容、資料庫案件；拒絕時資料不變
def _legacy_snapshot(invoice_id: int) -> tuple[object, ...]:
    inv = invoices_router._find_invoice(str(invoice_id))
    return tuple(
        inv.get(k)
        for k in (
            "review_status",
            "status",
            "purpose",
            "account_code",
            "deduction_code",
            "tax_type",
        )
    )


@pytest.mark.parametrize(
    "headers", [{}, {"Origin": "http://evil.example"}], ids=["no-origin", "cross"]
)
def test_legacy_review_requires_same_origin(
    finance_me: TestClient, headers: dict[str, str]
) -> None:
    _set_uploader(3, OTHER, "other@example.com")
    before = _legacy_snapshot(3)
    response = finance_me.post(
        "/invoices/3/review", headers=headers, data={"decision": "passed", "purpose": "x"}
    )
    assert response.status_code == 403
    assert _legacy_snapshot(3) == before


@pytest.mark.parametrize(
    "data",
    [
        {"decision": "approve-everything"},
        {"decision": ""},
        {"decision": "passed", "tax_type": "9 - 亂填"},
        {"decision": "passed", "deduction_code": "99"},
        {"decision": "passed", "purpose": "x" * 201},
    ],
    ids=["bad-decision", "empty-decision", "bad-tax", "bad-deduction", "too-long"],
)
def test_legacy_review_rejects_invalid_input_without_partial_change(
    finance_me: TestClient, data: dict[str, str]
) -> None:
    _set_uploader(3, OTHER, "other@example.com")
    before = _legacy_snapshot(3)
    response = finance_me.post("/invoices/3/review", headers=_ORIGIN, data=data)
    assert response.status_code == 400
    assert _legacy_snapshot(3) == before  # 沒有部分修改（含 purpose 等欄位）


def test_legacy_review_refuses_non_reviewable_status_and_keeps_data(
    finance_me: TestClient,
) -> None:
    _set_uploader(1, OTHER, "other@example.com")  # 示範案件 1 已核准
    before = _legacy_snapshot(1)
    response = finance_me.post(
        "/invoices/1/review", headers=_ORIGIN, data={"decision": "rejected", "purpose": "改寫"}
    )
    assert response.status_code == 400 and "不在待財務審核狀態" in response.text
    assert _legacy_snapshot(1) == before


def test_legacy_review_unknown_invoice_is_404(finance_me: TestClient) -> None:
    response = finance_me.post(
        "/invoices/999999/review", headers=_ORIGIN, data={"decision": "passed"}
    )
    assert response.status_code == 404


def test_legacy_review_refuses_database_invoices(
    finance_me: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """資料庫案件不會被舊入口寫入；回 409 而不是假裝成功。"""
    db_id = "5b0f8b0e-6c7a-4c55-9a1e-0a1b2c3d4e5f"
    invoice = dict(invoices_router.DEMO_TEST_INVOICES[2])
    invoice.update(id=db_id, uploaded_by=OTHER)
    monkeypatch.setattr(invoices_router.invoice_review_service, "fetch_invoice", lambda _i: invoice)
    response = finance_me.post(
        f"/invoices/{db_id}/review", headers=_ORIGIN, data={"decision": "passed"}
    )
    assert response.status_code == 409


def test_legacy_review_cannot_review_the_same_case_twice(finance_me: TestClient) -> None:
    """舊入口修改後審核案件快取必須失效；否則狀態檢核讀到過期的待審核，可重複核准或翻轉。"""
    _set_uploader(3, OTHER, "other@example.com")
    first = finance_me.post(
        "/invoices/3/review", headers=_ORIGIN, data={"decision": "passed"}, follow_redirects=False
    )
    assert first.status_code == 303
    again = finance_me.post(
        "/invoices/3/review", headers=_ORIGIN, data={"decision": "rejected"}, follow_redirects=False
    )
    assert again.status_code == 400 and "不在待財務審核狀態" in again.text
    assert invoices_router._find_invoice("3")["review_status"] == "approved"
