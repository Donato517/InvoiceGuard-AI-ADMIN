"""
模組名稱：tests.unit.test_procurement_pages
功能說明：步驟 3 採購退回補正（2-5）畫面與重送路由測試——退回項目顯示、逐項回覆檢查、重送後進入新一輪財務審核。
相依模組：app.routers.invoices、app.services.procurement_case_service
注意事項：
    - 使用 admin_client 測試替身登入，不連資料庫；示範發票 3 為待審核案件，送出者為 uploaded_by=1。
    - 每個測試結束後還原示範資料、審核狀態與退回快照，避免互相影響。
"""

from collections.abc import Iterator
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.routers import invoices as invoices_router

FINANCE_USER = "finance-9"


def _clear_state() -> None:
    invoices_router._FINANCE_CASES.clear()
    invoices_router._FINANCE_HISTORY.clear()
    invoices_router._RETURN_SNAPSHOTS.clear()


@pytest.fixture(autouse=True)
def _restore_demo_state() -> Iterator[None]:
    store = deepcopy(invoices_router._INVOICE_STORE)
    _clear_state()
    yield
    invoices_router._INVOICE_STORE[:] = store
    _clear_state()


def _login_as(user_id: str) -> None:
    from app.core.deps import require_admin
    from app.main import app

    def override(request: Request) -> SimpleNamespace:
        admin = SimpleNamespace(id=user_id, display_name="測試人員", role="admin", is_active=True)
        request.state.admin = admin
        return admin

    app.dependency_overrides[require_admin] = override


@pytest.fixture
def client(admin_client: TestClient) -> TestClient:
    """預設以財務人員退回發票 3，測試內再切換為申請人（uploaded_by=1）。"""
    _login_as(FINANCE_USER)
    response = admin_client.post(
        "/invoices/3/finance-review/return",
        data={
            "return_field": ["total_amount", "attachment"],
            "return_reason": ["請依發票修正總計", "補上採購單"],
        },
    )
    assert response.status_code == 303, response.text[-300:]
    _login_as("1")
    return admin_client


def _correct(client: TestClient, **changes: object) -> None:
    invoice = invoices_router._find_invoice(3)
    data = {
        "invoice_number": invoice["invoice_number"],
        "invoice_date": invoice["invoice_date"],
        "net_amount": invoice["net_amount"],
        "tax_amount": invoice["tax_amount"],
        "total_amount": invoice["total_amount"],
        "seller_name": invoice["seller_name"],
        "seller_tax_id": invoice["seller_tax_id"],
    }
    data.update(changes)
    response = client.post("/invoices/3/flow/correct", data=data)
    assert response.status_code == 303
    assert response.headers["location"] == "/invoices/3/flow?step=3"


def _resubmit(client: TestClient, answers: dict[str, tuple[str, str]]) -> httpx.Response:
    keys = ["total_amount|", "attachment|"]
    return client.post(
        "/invoices/3/procurement/resubmit",
        data={
            "response_key": keys,
            "response_action": [answers.get(k, ("", ""))[0] for k in keys],
            "response_note": [answers.get(k, ("", ""))[1] for k in keys],
        },
    )


def test_step3_shows_returned_items(client: TestClient) -> None:
    html = client.get("/invoices/3/flow?step=3").text
    assert "退回補正" in html
    assert "總計</strong>：請依發票修正總計" in html
    assert "附件</strong>：補上採購單" in html
    assert "儲存補正內容" in html
    assert "returned_to_applicant" not in html
    assert "total_amount</strong>" not in html


def test_invoice_list_marks_returned_first(client: TestClient) -> None:
    html = client.get("/invoices").text
    assert "待補正" in html
    assert html.index("AB12345680") < html.index("AB12345678")


def test_resubmit_requires_every_item_answered(client: TestClient) -> None:
    _correct(client, total_amount=1575, net_amount=1500, tax_amount=75)
    response = _resubmit(client, {"total_amount|": ("appealed", "原發票即為 1575")})
    assert response.status_code == 400
    assert "尚有退回項目未回覆：附件" in response.text
    assert invoices_router._find_invoice(3)["review_status"] == "returned"


def test_corrected_without_change_is_blocked(client: TestClient) -> None:
    response = _resubmit(
        client,
        {"total_amount|": ("corrected", ""), "attachment|": ("corrected", "")},
    )
    assert response.status_code == 400
    assert "總計 回覆已補正，但內容沒有修改" in response.text
    assert "附件 回覆已補正，但內容沒有修改" in response.text


def test_appeal_requires_note(client: TestClient) -> None:
    response = _resubmit(
        client,
        {"total_amount|": ("appealed", ""), "attachment|": ("appealed", "已附於紙本")},
    )
    assert response.status_code == 400
    assert "總計 申覆時須說明理由" in response.text


def test_changed_field_and_reupload_count_as_corrected(client: TestClient) -> None:
    _correct(client, net_amount=1600, tax_amount=80, total_amount=1680)
    upload = client.post(
        "/invoices/3/flow/upload",
        files={"file": ("new.png", b"\x89PNG new attachment", "image/png")},
    )
    assert upload.status_code == 303
    html = client.get("/invoices/3/flow?step=3").text
    assert html.count("已修改</span>") == 2


def test_successful_resubmit_starts_round_two(client: TestClient) -> None:
    _correct(client, net_amount=1600, tax_amount=80, total_amount=1680)
    response = _resubmit(
        client,
        {
            "total_amount|": ("corrected", "已依發票修正"),
            "attachment|": ("appealed", "採購單已由紙本送財務"),
        },
    )
    assert response.status_code == 303
    invoice = invoices_router._find_invoice(3)
    assert (invoice["review_status"], invoice["stage"]) == ("pending", 4)
    case = invoices_router._FINANCE_CASES[3]
    assert (case.status, case.submission_round) == ("pending_department_approval", 2)
    assert all(not r.is_checked and r.review_comment is None for r in case.field_reviews)
    total = next(r for r in case.field_reviews if r.field_name == "total_amount")
    assert total.submitted_value == "1680"
    assert invoices_router._RETURN_SNAPSHOTS == {}

    _login_as(FINANCE_USER)
    html = client.get("/invoices/3/flow?step=5").text
    assert "第 2 次送出" in html
    assert "申請人重新送出" in html
    assert "總計：已補正－已依發票修正" in html
    assert "附件：申覆（不修改）－採購單已由紙本送財務" in html
    assert "確認核准" in html


def test_resubmit_rejected_when_not_returned(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/invoices/4/procurement/resubmit",
        data={"response_key": ["sales_amount|"], "response_action": ["corrected"]},
    )
    assert response.status_code == 400
    assert "只有待補正的案件可以重送" in response.text
    assert invoices_router._find_invoice(4)["review_status"] == "pending"


def test_step3_without_return_has_no_return_panel(admin_client: TestClient) -> None:
    html = admin_client.get("/invoices/3/flow?step=3").text
    assert "重新送出財務審核" not in html
    assert "儲存並進入系統檢核" in html
