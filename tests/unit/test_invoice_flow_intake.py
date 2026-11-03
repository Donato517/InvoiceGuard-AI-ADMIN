# ruff: noqa: F811
"""測試目標：發票作業流程步驟 1～4 串接 API 辨識（Document AI OCR → 模型整理）、檢核與提交。

API 以 httpx.MockTransport 的假 API 替身（沿用 test_intake_routes）；合成資料，不連 API、DB 或 Storage。
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_intake_routes import INV, RID, FakeApi, api, review_data  # noqa: F401

from app.routers import invoices as invoices_router

_ORIGIN = {"Origin": "http://testserver"}  # 流程頁的上傳／核對 POST 需同源

PNG = ("invoice.png", b"fake-png-bytes", "image/png")


@pytest.fixture
def flow_api(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    monkeypatch.setattr(invoices_router, "get_intake_client", lambda request: api.client())
    return api


@pytest.fixture
def draft(admin_client: TestClient) -> Iterator[str]:
    response = admin_client.get("/invoices/new")
    assert response.status_code == 303
    location = response.headers["location"]
    yield location.split("/")[2]


def _upload(admin_client: TestClient, invoice_id: str) -> Any:
    return admin_client.post(
        f"/invoices/{invoice_id}/flow/upload", headers=_ORIGIN, files={"file": PNG}
    )


def test_upload_calls_api_recognize_and_shows_result_on_step2(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    response = _upload(admin_client, draft)

    assert response.status_code == 303
    assert response.headers["location"] == f"/invoices/{draft}/flow?step=2"
    assert flow_api.paths() == ["/api/v1/invoices/recognize"]
    html = admin_client.get(f"/invoices/{draft}/flow?step=2").text
    assert "12345678" in html and "辦公文具" not in html  # 明細在步驟 3
    assert "買受人統編未辨識，請輸入" in html
    assert "範例科技股份有限公司" not in html  # 不再顯示示範預設值


def test_step3_uses_recognized_values_and_check_calls_the_three_api_checks(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)

    step3 = admin_client.get(f"/invoices/{draft}/flow?step=3").text
    assert 'value="辦公文具"' in step3
    assert f'action="/invoices/{draft}/flow/review"' in step3

    checked = admin_client.post(
        f"/invoices/{draft}/flow/review",
        headers=_ORIGIN,
        data=review_data("check", recognition_id=RID),
    )

    assert checked.status_code == 200
    assert 'data-state="checked"' in checked.text
    assert flow_api.paths()[1:] == [
        "/api/v1/invoices/check-required",
        "/api/v1/invoices/check-warnings",
        "/api/v1/invoices/check-duplicate",
    ]


def test_submit_creates_case_clears_image_and_step4_links_to_it(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)

    response = admin_client.post(
        f"/invoices/{draft}/flow/review",
        headers=_ORIGIN,
        data=review_data("submit", recognition_id=RID),
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/invoices/{draft}/flow?step=4"
    assert flow_api.paths()[-1] == "/api/v1/invoices"
    invoice = invoices_router._find_flow_invoice(draft)
    assert invoice is not None and "file_bytes" not in invoice
    step4 = admin_client.get(f"/invoices/{draft}/flow?step=4").text
    assert f"/cases/{INV}" in step4
    assert f"/invoices/{draft}/flow/advance" not in step4  # 不再進入示範的財務步驟


def test_review_after_submit_does_not_call_api_again(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)
    admin_client.post(f"/invoices/{draft}/flow/review", headers=_ORIGIN, data=review_data("submit"))
    calls = len(flow_api.requests)

    again = admin_client.post(
        f"/invoices/{draft}/flow/review", headers=_ORIGIN, data=review_data("submit")
    )

    assert again.status_code == 303
    assert len(flow_api.requests) == calls


def test_upload_without_api_token_is_401_and_nothing_is_sent(
    admin_client: TestClient, draft: str
) -> None:
    response = _upload(admin_client, draft)

    assert response.status_code == 401


def test_api_failure_stays_on_step1_with_message(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    import httpx

    flow_api.routes["/api/v1/invoices/recognize"] = httpx.Response(
        422, json={"error": {"code": "INVOICE_TEXT_NOT_FOUND", "message": "圖片中沒有文字"}}
    )

    response = _upload(admin_client, draft)

    assert response.status_code == 200
    assert "圖片中沒有文字" in response.text


def test_flow_posts_without_same_origin_are_rejected(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    upload = admin_client.post(f"/invoices/{draft}/flow/upload", files={"file": PNG})
    review = admin_client.post(f"/invoices/{draft}/flow/review", data=review_data("check"))

    assert upload.status_code == 403 and review.status_code == 403
    assert flow_api.requests == []
