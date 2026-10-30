"""
模組名稱：tests.unit.test_finance_review_pages
功能說明：步驟 5 財務審核（2-4）畫面與按鈕路由測試——逐欄確認、提醒確認、會計欄位、核准、退回與駁回。
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
    for field_name in ("total_amount", "seller_name"):
        response = client.post("/invoices/3/finance-review/fields", data={"field_name": field_name})
        assert response.status_code == 303, response.text[-300:]
    acknowledged = client.post(
        "/invoices/3/finance-review/warnings",
        data={"code": "tax_id_invalid", "field": "seller_tax_id", "note": "已向供應商確認"},
    )
    assert acknowledged.status_code == 303


def test_step5_shows_field_reviews_warnings_and_trails(finance_client: TestClient) -> None:
    html = _step5(finance_client)

    assert "修改欄位確認（AI 原值 → 送出值）" in html
    assert "總計" in html and "1557" in html and "1575" in html
    assert "tax_id_invalid" in html
    assert "1557 → 1755 → 1575" in html
    assert "核准前待處理（2）" in html
    assert "請選擇扣抵代號" not in html  # 示範發票 3 已有扣抵代號


def test_approve_blocked_until_items_handled(finance_client: TestClient) -> None:
    blocked = finance_client.post("/invoices/3/finance-review/approve")

    assert blocked.status_code == 400
    assert "個修改欄位未確認" in blocked.text
    assert invoices_router._find_invoice(3)["review_status"] == "pending"


def test_full_review_then_approve_updates_invoice(finance_client: TestClient) -> None:
    _prepare_for_approval(finance_client)

    assert "核准前待處理" not in _step5(finance_client)
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
        data={"tax_type": "zero_rated", "deduction_code": "1", "account_code": "5601"},
    )

    assert response.status_code == 303
    invoice = invoices_router._find_invoice(4)
    assert invoice["tax_category"] == "零稅率"
    assert invoice["deduction_code"] == "1 - 進貨及費用"
    assert invoice["account_code"] == "5601 辦公用品費"


def test_account_code_fills_default_deduction_code(finance_client: TestClient) -> None:
    """示範發票 3 原本扣抵代號 1，改選固定資產科目且不指定扣抵代號時帶出 2。"""
    response = finance_client.post(
        "/invoices/3/finance-review/accounting", data={"account_code": "1401"}
    )

    assert response.status_code == 303
    invoice = invoices_router._find_invoice(3)
    assert invoice["account_code"] == "1401 辦公設備"
    assert invoice["deduction_code"] == "2 - 固定資產"
    assert 'value="1401"' in _step5(finance_client)


def test_account_and_deduction_code_must_match(finance_client: TestClient) -> None:
    """選 1 進貨及費用時不能用固定資產科目。"""
    response = finance_client.post(
        "/invoices/3/finance-review/accounting",
        data={"account_code": "1401", "deduction_code": "1"},
    )

    assert response.status_code == 400
    assert "與所選扣抵代號 1 不同" in response.text
    assert invoices_router._find_invoice(3)["account_code"].startswith("5602")


def test_step5_lists_only_accounts_of_current_deduction_code(finance_client: TestClient) -> None:
    """示範發票 3 扣抵代號為 1：固定資產與不得扣抵科目預設隱藏。"""
    html = _step5(finance_client)
    options = re.findall(r'<option value="(\d{4})"([^>]*)>', html)
    hidden = {code for code, attrs in options if "hidden" in attrs}
    visible = {code for code, attrs in options if "hidden" not in attrs}

    assert visible == {"5601", "5602", "5605"}
    assert hidden == {"5611", "5612", "1401", "1421"}


def test_unknown_account_code_rejected(finance_client: TestClient) -> None:
    response = finance_client.post(
        "/invoices/3/finance-review/accounting", data={"account_code": "9999"}
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


def test_reject_requires_reason_then_closes_case(finance_client: TestClient) -> None:
    assert finance_client.post("/invoices/4/finance-review/reject").status_code == 400

    response = finance_client.post(
        "/invoices/4/finance-review/reject", data={"reason": "非公司費用"}
    )

    assert response.status_code == 303
    assert invoices_router._find_invoice(4)["review_status"] == "rejected"
    assert "此請款單已駁回，不能再重送。" in _step5(finance_client, 4)


def test_approved_invoice_cannot_be_reviewed_again(finance_client: TestClient) -> None:
    response = finance_client.post(
        "/invoices/1/finance-review/fields", data={"field_name": "total_amount"}
    )

    assert response.status_code == 400
    assert "不在待財務審核狀態" in response.text
