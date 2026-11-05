"""
模組名稱：tests.unit.test_self_upload_flow_gates
功能說明：本人上傳的發票不得查看步驟 5、6，也不得從步驟 4 往後推進；步驟 4 改顯示「檢視我的發票」。
相依模組：app.routers.invoices
注意事項：
    - 2026-11-06 使用者確認的正式需求，取代先前「本人可進入步驟 5 檢視、只是不能操作」的設計。
    - 核准、退回、儲存會計欄位與舊 /review 的本人限制由 test_finance_self_upload 涵蓋。
    - 只用合成 UUID 與示範資料，不連資料庫；離線測試，不是正式環境驗收。
"""

from collections.abc import Iterator
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.routers import invoices as invoices_router

ME = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
STRANGER = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def _restore_demo_state() -> Iterator[None]:
    store = deepcopy(invoices_router._INVOICE_STORE)
    yield
    invoices_router._INVOICE_STORE[:] = store
    invoices_router._FINANCE_CASES.clear()
    invoices_router._FINANCE_HISTORY.clear()
    invoices_router._RETURN_SNAPSHOTS.clear()


def _login(user_id: str) -> None:
    from app.core.deps import require_admin
    from app.main import app

    def as_user(request: Request) -> SimpleNamespace:
        admin = SimpleNamespace(id=user_id, display_name="測試人員", role="admin", is_active=True)
        request.state.admin = admin
        return admin

    app.dependency_overrides[require_admin] = as_user


@pytest.fixture
def finance_me(admin_client: TestClient) -> TestClient:
    _login(ME)
    return admin_client


def _invoice(invoice_id: int = 3, *, uploader: str, stage: int = 5) -> dict:
    invoice = invoices_router._find_invoice(str(invoice_id))
    invoice["uploaded_by"] = uploader
    invoice["stage"] = stage
    invoice["is_completed"] = False
    return invoice


# ---- 本人：不得查看步驟 5、6 起的財務頁 ----
@pytest.mark.parametrize("step", [5, 6, 7, 8, 9])
def test_self_upload_cannot_view_finance_steps(finance_me: TestClient, step: int) -> None:
    _invoice(uploader=ME, stage=9)
    assert finance_me.get(f"/invoices/3/flow?step={step}").status_code == 403


def test_self_upload_can_still_view_steps_up_to_four(finance_me: TestClient) -> None:
    _invoice(uploader=ME, stage=4)
    for step in (3, 4):
        assert finance_me.get(f"/invoices/3/flow?step={step}").status_code == 200


# ---- 本人：不得往後推進 ----
@pytest.mark.parametrize("from_step", [4, 5, 6, 7, 8])
def test_self_upload_cannot_advance_from_step_four_or_later(
    finance_me: TestClient, from_step: int
) -> None:
    invoice = _invoice(uploader=ME, stage=from_step)
    response = finance_me.post("/invoices/3/flow/advance", data={"from_step": from_step})
    assert response.status_code == 403
    assert invoice["stage"] == from_step
    assert invoice["review_status"] == "pending" and invoice["status"] == "flagged"


def test_self_upload_cannot_complete_flow(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=ME, stage=8)
    assert finance_me.post("/invoices/3/flow/complete").status_code == 403
    assert invoice["is_completed"] is False and invoice["stage"] == 8


def test_self_review_post_error_does_not_render_review_step(finance_me: TestClient) -> None:
    """直接 POST 核准被拒時，回應不得再呈現步驟 5 的審核內容。"""
    _invoice(uploader=ME, stage=5)
    response = finance_me.post("/invoices/3/finance-review/approve")
    assert response.status_code == 403
    assert "不得由您自行核准、退回或儲存會計欄位" in response.text
    assert "核對欄位（14 項）" not in response.text
    assert 'action="/invoices/3/finance-review/approve"' not in response.text


# ---- 本人：步驟 4 的按鈕與導覽 ----
def test_self_upload_step4_shows_view_my_invoice_to_invoice_list(finance_me: TestClient) -> None:
    _invoice(uploader=ME, stage=4)
    html = finance_me.get("/invoices/3/flow?step=4").text
    assert "檢視我的發票" in html
    assert 'href="/invoices" class="btn btn--primary" data-view-my-invoice' in html
    assert "確認並繼續" not in html
    assert 'action="/invoices/3/flow/advance"' not in html
    assert "前往步驟 5 財務審核" not in html
    assert "/flow?step=5" not in html


def test_view_my_invoice_target_is_the_invoice_management_list(finance_me: TestClient) -> None:
    _invoice(uploader=ME, stage=4)
    response = finance_me.get("/invoices")
    assert response.status_code == 200
    assert "發票管理" in response.text  # 完整發票管理列表（財務部可見所有發票）
    assert "/invoices/3" in response.text


def test_self_upload_stepper_has_no_finance_steps(finance_me: TestClient) -> None:
    _invoice(uploader=ME, stage=9)
    html = finance_me.get("/invoices/3/flow?step=4").text
    assert "/flow?step=5" not in html and "/flow?step=6" not in html


def test_list_link_for_self_upload_stops_at_step_four(finance_me: TestClient) -> None:
    _invoice(uploader=ME, stage=7)
    html = finance_me.get("/invoices").text
    assert "/invoices/3/flow?step=4" in html
    assert "/invoices/3/flow?step=5" not in html and "/invoices/3/flow?step=7" not in html


# ---- 其他審核者：照常 ----
def test_other_reviewer_can_view_and_advance_finance_steps(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=OTHER, stage=4)
    assert finance_me.get("/invoices/3/flow?step=5").status_code == 200
    assert finance_me.get("/invoices/3/flow?step=6").status_code == 200
    to_review = finance_me.post("/invoices/3/flow/advance", data={"from_step": 4})
    assert (to_review.status_code, to_review.headers["location"]) == (
        303,
        "/invoices/3/flow?step=5",
    )
    to_filing = finance_me.post("/invoices/3/flow/advance", data={"from_step": 5})
    assert (to_filing.status_code, to_filing.headers["location"]) == (
        303,
        "/invoices/3/flow?step=6",
    )
    assert invoice["stage"] == 6
    assert finance_me.post("/invoices/3/flow/complete").status_code == 303


def test_other_reviewer_step4_keeps_continue_button(finance_me: TestClient) -> None:
    _invoice(uploader=OTHER, stage=4)
    html = finance_me.get("/invoices/3/flow?step=4").text
    assert "確認並繼續" in html and "檢視我的發票" not in html


def test_non_finance_user_still_cannot_reach_others_invoice(admin_client: TestClient) -> None:
    """沒有財務權限的人，不因本次修改取得他人發票或步驟 5、6 的存取權。"""
    _login(STRANGER)
    request_state = {"is_finance_workspace": False, "invoice_max_step": 4}

    def deny_non_finance(request: Request) -> None:
        request.state.invoice_user_id = STRANGER
        request.state.is_finance_workspace = request_state["is_finance_workspace"]
        request.state.invoice_max_step = request_state["invoice_max_step"]

    from app.main import app

    app.dependency_overrides[invoices_router.require_invoice_access] = deny_non_finance
    try:
        _invoice(uploader=OTHER, stage=5)
        assert admin_client.get("/invoices/3/flow?step=4").status_code == 403
        assert admin_client.get("/invoices/3/flow?step=5").status_code == 403
        assert admin_client.get("/invoices/3").status_code == 403
        assert (
            admin_client.post("/invoices/3/flow/advance", data={"from_step": 4}).status_code == 403
        )
    finally:
        app.dependency_overrides.pop(invoices_router.require_invoice_access, None)


# ---- 資料庫發票的 UUID 比對（不只靠示範帳號）----
@pytest.mark.parametrize(
    "uploader",
    [ME, ME.upper(), UUID(ME), f" {ME} "],
    ids=["lower", "upper", "uuid-object", "padded"],
)
def test_is_self_upload_matches_real_uuid_forms(uploader: object) -> None:
    assert invoices_router._is_self_upload(uploader, ME) is True


def test_is_self_upload_ignores_demo_placeholder_and_other_users() -> None:
    assert invoices_router._is_self_upload("1", "1") is False  # 示範整數佔位 ID 不算本人
    assert invoices_router._is_self_upload(OTHER, ME) is False
    assert invoices_router._is_self_upload(None, ME) is False


def test_database_invoice_with_uppercase_uploader_uuid_is_denied(
    finance_me: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """資料庫來源的發票：uploaded_by 取自 submitted_by（文字 UUID），大小寫不同仍視為本人。"""
    db_id = "5b0f8b0e-6c7a-4c55-9a1e-0a1b2c3d4e5f"
    row = deepcopy(invoices_router.DEMO_TEST_INVOICES[2])
    row.update({"id": db_id, "uploaded_by": ME.upper(), "stage": 5})
    monkeypatch.setattr(
        invoices_router.invoice_review_service, "fetch_invoice", lambda invoice_id: row
    )
    for step in (5, 6):
        assert finance_me.get(f"/invoices/{db_id}/flow?step={step}").status_code == 403
    assert (
        finance_me.post(f"/invoices/{db_id}/flow/advance", data={"from_step": 4}).status_code == 403
    )
    assert finance_me.post(f"/invoices/{db_id}/flow/complete").status_code == 403
