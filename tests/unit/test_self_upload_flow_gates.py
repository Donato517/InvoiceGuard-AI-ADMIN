"""
模組名稱：tests.unit.test_self_upload_flow_gates
功能說明：本人上傳的發票不得自行推進財務流程——步驟 5 起的「確認並繼續」與「確認完成」。
相依模組：app.routers.invoices
注意事項：
    - 核准、退回、儲存會計欄位與舊 /review 的本人限制由 test_finance_self_upload 涵蓋，這裡只測流程推進。
    - 只用合成 UUID 與示範發票，不連資料庫；離線測試，不是正式環境驗收。
"""

from collections.abc import Iterator
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.routers import invoices as invoices_router

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


def _invoice(invoice_id: int = 3, *, uploader: str, stage: int = 5) -> dict:
    invoice = invoices_router._find_invoice(str(invoice_id))
    invoice["uploaded_by"] = uploader
    invoice["stage"] = stage
    invoice["is_completed"] = False
    return invoice


@pytest.mark.parametrize("from_step", [5, 6, 7, 8])
def test_self_upload_cannot_advance_from_finance_steps(
    finance_me: TestClient, from_step: int
) -> None:
    invoice = _invoice(uploader=ME, stage=from_step)
    response = finance_me.post("/invoices/3/flow/advance", data={"from_step": from_step})
    assert response.status_code == 403
    assert invoice["stage"] == from_step


def test_self_upload_can_still_move_from_check_to_review_view(finance_me: TestClient) -> None:
    """步驟 4→5 只是進到可檢視的審核頁（審核動作另有限制），不屬於放行。"""
    _invoice(uploader=ME, stage=4)
    response = finance_me.post("/invoices/3/flow/advance", data={"from_step": 4})
    assert response.status_code == 303
    assert response.headers["location"] == "/invoices/3/flow?step=5"


def test_other_uploader_can_advance_from_review(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=OTHER, stage=5)
    response = finance_me.post("/invoices/3/flow/advance", data={"from_step": 5})
    assert response.status_code == 303
    assert response.headers["location"] == "/invoices/3/flow?step=6"
    assert invoice["stage"] == 6


def test_self_upload_cannot_complete_flow(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=ME, stage=8)
    assert finance_me.post("/invoices/3/flow/complete").status_code == 403
    assert invoice["is_completed"] is False
    assert invoice["stage"] == 8


def test_other_uploader_can_complete_flow(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=OTHER, stage=8)
    response = finance_me.post("/invoices/3/flow/complete")
    assert response.status_code == 303
    assert invoice["is_completed"] is True


@pytest.mark.parametrize("step", [5, 6])
def test_flow_footer_hides_advance_for_self_upload(finance_me: TestClient, step: int) -> None:
    _invoice(uploader=ME, stage=9)
    html = finance_me.get(f"/invoices/3/flow?step={step}").text
    assert "data-self-upload-no-advance" in html
    assert 'action="/invoices/3/flow/advance"' not in html


def test_flow_footer_keeps_advance_for_other_uploader(finance_me: TestClient) -> None:
    _invoice(uploader=OTHER, stage=9)
    html = finance_me.get("/invoices/3/flow?step=5").text
    assert "data-self-upload-no-advance" not in html
    assert 'action="/invoices/3/flow/advance"' in html
