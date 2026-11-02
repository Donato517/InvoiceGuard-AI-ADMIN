"""
模組名稱：tests.unit.test_finance_review_pages
功能說明：步驟 5 財務審核（2-4）畫面與按鈕路由測試——提醒確認、費用大項與會計科目、修改歷程、核准與退回。
相依模組：app.routers.invoices、app.services.finance_review_service
注意事項：
    - 使用 admin_client 測試替身登入，不連資料庫；示範發票 3、4 為待審核案件。
    - 每個測試結束後還原示範資料與審核狀態，避免互相影響。
"""

import re
from collections.abc import Iterator
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.routers import invoices as invoices_router

FINANCE_USER = "finance-9"


@pytest.fixture(autouse=True)
def _restore_demo_state() -> Iterator[None]:
    store = deepcopy(invoices_router._INVOICE_STORE)
    yield
    invoices_router._INVOICE_STORE[:] = store
    invoices_router._FINANCE_CASES.clear()
    invoices_router._FINANCE_HISTORY.clear()
    invoices_router._RETURN_SNAPSHOTS.clear()


@pytest.fixture
def finance_client(admin_client: TestClient) -> TestClient:
    """財務人員（不是送出者）：示範發票的送出者為 uploaded_by=1。"""
    from app.core.deps import require_admin
    from app.main import app

    def as_finance(request: Request) -> SimpleNamespace:
        admin = SimpleNamespace(
            id=FINANCE_USER, display_name="財務人員", role="admin", is_active=True
        )
        request.state.admin = admin
        return admin

    app.dependency_overrides[require_admin] = as_finance
    return admin_client


def _step5(client: TestClient, invoice_id: int = 3) -> str:
    response = client.get(f"/invoices/{invoice_id}/flow?step=5")
    assert response.status_code == 200
    return response.text


def _prepare_for_approval(client: TestClient) -> None:
    acknowledged = client.post(
        "/invoices/3/finance-review/warnings",
        data={"code": "tax_id_invalid", "field": "seller_tax_id", "note": "已向供應商確認"},
    )
    assert acknowledged.status_code == 303


def test_step5_shows_warnings_and_history_link(finance_client: TestClient) -> None:
    html = _step5(finance_client)

    assert "tax_id_invalid" in html
    assert 'data-open-dialog="change-history-modal"' in html
    assert ">修改歷程</a>" in html
    assert "請選擇扣抵代號" not in html  # 示範發票 3 已有扣抵代號


def test_step5_history_modal_lists_applicant_changes(finance_client: TestClient) -> None:
    html = _step5(finance_client)
    modal = html[html.index('id="change-history-modal"') : html.index("</dialog>")]

    assert "1557 → 1755 → 1575" in modal
    assert "總計" in modal


def test_step5_removed_sections(finance_client: TestClient) -> None:
    """2026-11-02 起移除核准前待處理、修改欄位確認、申請人修改軌跡、駁回與採購用途。"""
    html = _step5(finance_client)

    for removed in (
        "核准前待處理",
        "修改欄位確認",
        "申請人修改軌跡",
        "駁回",
        "採購用途",
        "finance-review/fields",
        "finance-review/reject",
    ):
        assert removed not in html


def test_removed_routes_are_gone(finance_client: TestClient) -> None:
    for path in ("/invoices/3/finance-review/fields", "/invoices/3/finance-review/reject"):
        assert finance_client.post(path, data={"field_name": "x", "reason": "y"}).status_code in (
            404,
            405,
        )


def test_approve_blocked_until_warning_acknowledged(finance_client: TestClient) -> None:
    blocked = finance_client.post("/invoices/3/finance-review/approve")

    assert blocked.status_code == 400
    assert "項檢核提醒未確認" in blocked.text
    assert "修改欄位未確認" not in blocked.text
    assert invoices_router._find_invoice(3)["review_status"] == "pending"


def test_approve_without_field_checks_updates_invoice(finance_client: TestClient) -> None:
    """修改欄位不需逐欄確認，確認檢核提醒後即可核准。"""
    _prepare_for_approval(finance_client)

    response = finance_client.post("/invoices/3/finance-review/approve")

    assert response.status_code == 303
    invoice = invoices_router._find_invoice(3)
    assert (invoice["review_status"], invoice["status"]) == ("approved", "passed")
    assert "此發票已完成審核（已核准）。" in _step5(finance_client)


def test_self_approval_blocked(admin_client: TestClient) -> None:
    """測試替身的使用者 ID 預設為 1，與示範發票的送出者相同。"""
    _prepare_for_approval(admin_client)

    response = admin_client.post("/invoices/3/finance-review/approve")

    assert response.status_code == 400
    assert "不能核准自己送出的請款單" in response.text


def test_warning_requires_note(finance_client: TestClient) -> None:
    response = finance_client.post(
        "/invoices/3/finance-review/warnings",
        data={"code": "tax_id_invalid", "field": "seller_tax_id", "note": " "},
    )

    assert response.status_code == 400
    assert "確認提醒時須填寫備註" in response.text


def test_accounting_fields_update_invoice(finance_client: TestClient) -> None:
    response = finance_client.post(
        "/invoices/4/finance-review/accounting",
        data={
            "tax_type": "zero_rated",
            "deduction_code": "1",
            "expense_category": "行政總務費用",
            "account_code": "6211004",
        },
    )

    assert response.status_code == 303
    invoice = invoices_router._find_invoice(4)
    assert invoice["tax_category"] == "零稅率"
    assert invoice["deduction_code"] == "1 - 進貨及費用"
    assert invoice["expense_category"] == "行政總務費用"
    assert invoice["account_code"] == "6211004 管理-文具用品"


def test_deduction_code_independent_of_account(finance_client: TestClient) -> None:
    """扣抵代號不再由會計科目帶出，也不檢查是否同類。"""
    response = finance_client.post(
        "/invoices/3/finance-review/accounting",
        data={"expense_category": "固定資產", "account_code": "1401090", "deduction_code": "1"},
    )

    assert response.status_code == 303
    invoice = invoices_router._find_invoice(3)
    assert invoice["account_code"] == "1401090 辦公設備-其他"
    assert invoice["deduction_code"] == "1 - 進貨及費用"


def test_account_requires_category_first(finance_client: TestClient) -> None:
    response = finance_client.post(
        "/invoices/3/finance-review/accounting", data={"account_code": "6211004"}
    )

    assert response.status_code == 400
    assert "請先選擇費用大項" in response.text
    assert invoices_router._find_invoice(3)["account_code"].startswith("6235090")


def test_account_must_belong_to_category(finance_client: TestClient) -> None:
    response = finance_client.post(
        "/invoices/3/finance-review/accounting",
        data={"expense_category": "用人費用", "account_code": "6211004"},
    )

    assert response.status_code == 400
    assert "與所選費用大項「用人費用」不同" in response.text


def test_step5_lists_only_accounts_of_current_category(finance_client: TestClient) -> None:
    """示範發票 3 的科目 6235090 屬於行政總務費用：預選該大項，只列出該大項的科目。"""
    html = _step5(finance_client)
    category_select = html[html.index('id="expense_category"') : html.index('id="account_code"')]
    selected = re.search(r'<option value="([^"]+)"\s+selected>', category_select)
    options = re.findall(r'<option value="(\d{7})"\s+data-category="([^"]+)"([^>]*)>', html)
    visible = {category for _, category, attrs in options if "hidden" not in attrs}

    assert selected is not None and selected.group(1) == "行政總務費用"
    assert visible == {"行政總務費用"}
    assert len(options) == 296
    assert 'value="6235090"' in html


def test_unknown_account_code_rejected(finance_client: TestClient) -> None:
    response = finance_client.post(
        "/invoices/3/finance-review/accounting",
        data={"expense_category": "用人費用", "account_code": "9999"},
    )

    assert response.status_code == 400
    assert "會計科目不在科目清單中" in response.text


def test_accounting_rejects_unknown_code(finance_client: TestClient) -> None:
    response = finance_client.post(
        "/invoices/4/finance-review/accounting", data={"deduction_code": "9"}
    )

    assert response.status_code == 400
    assert "扣抵代號只能是 1～4" in response.text


def test_return_writes_field_comment_and_history(finance_client: TestClient) -> None:
    response = finance_client.post(
        "/invoices/3/finance-review/return",
        data={
            "return_field": ["total_amount", "attachment", ""],
            "return_reason": ["請附折讓單", "補上採購單", ""],
        },
    )

    assert response.status_code == 303
    invoice = invoices_router._find_invoice(3)
    assert invoice["review_status"] == "returned"
    case = invoices_router._FINANCE_CASES[3]
    total = next(r for r in case.field_reviews if r.field_name == "total_amount")
    assert (total.is_checked, total.review_comment) == (False, "請附折讓單")
    (change,) = invoices_router._FINANCE_HISTORY[3]
    assert change.reason == "attachment：補上採購單"
    html = _step5(finance_client)
    assert "已退回申請單位補正" in html
    assert "確認核准" not in html


def test_return_without_items_rejected(finance_client: TestClient) -> None:
    response = finance_client.post(
        "/invoices/3/finance-review/return",
        data={"return_field": [""], "return_reason": [""]},
    )

    assert response.status_code == 400
    assert "退回時須逐項填寫欄位與補正原因" in response.text


def test_approved_invoice_cannot_be_reviewed_again(finance_client: TestClient) -> None:
    response = finance_client.post(
        "/invoices/1/finance-review/warnings",
        data={"code": "tax_id_invalid", "field": "seller_tax_id", "note": "再次確認"},
    )

    assert response.status_code == 400
    assert "不在待財務審核狀態" in response.text
