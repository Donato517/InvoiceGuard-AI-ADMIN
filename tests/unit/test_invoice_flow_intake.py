# ruff: noqa: F811
"""測試目標：發票作業流程步驟 1～4 串接 API 辨識（Document AI OCR → 模型整理）、檢核與提交。

API 以 httpx.MockTransport 的假 API 替身（沿用 test_intake_routes）；合成資料，不連 API、DB 或 Storage。
"""

import copy
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from test_intake_routes import INV, RECOGNITION, RID, FakeApi, api, review_data  # noqa: F401

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


def test_upload_calls_api_recognize_and_shows_result_on_step3(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    response = _upload(admin_client, draft)

    assert response.status_code == 303
    assert response.headers["location"] == f"/invoices/{draft}/flow?step=3"
    assert flow_api.paths() == ["/api/v1/invoices/recognize"]
    # 步驟 2 已跳過並直接重導向至步驟 3
    step2_res = admin_client.get(f"/invoices/{draft}/flow?step=2")
    assert step2_res.status_code == 303
    assert step2_res.headers["location"] == f"/invoices/{draft}/flow?step=3"
    html = admin_client.get(f"/invoices/{draft}/flow?step=3").text
    assert "12345678" in html
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


def test_step3_shows_original_image_beside_the_form_and_no_check_button(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)

    html = admin_client.get(f"/invoices/{draft}/flow?step=3").text

    assert "invoice-correction-split" in html
    assert f'src="/invoices/{draft}/file"' in html  # 左側原圖
    assert 'id="flow-review-form"' in html
    assert f'data-live-check-url="/invoices/{draft}/flow/check"' in html
    assert 'value="check"' not in html  # 不需要「檢核」按鈕
    assert "invoice-flow-review.js" in html
    assert 'class="field-required">*必填</span>' in html


def test_live_check_returns_per_field_results_as_json(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    import httpx

    flow_api.routes["/api/v1/invoices/check-required"] = httpx.Response(
        200,
        json={
            "required_complete": False,
            "missing": [
                {
                    "field": "seller_tax_id",
                    "code": "required_missing",
                    "message": "x",
                    "line_no": None,
                }
            ],
        },
    )
    _upload(admin_client, draft)

    response = admin_client.post(
        f"/invoices/{draft}/flow/check", headers=_ORIGIN, data=review_data("check")
    )

    body = response.json()
    assert response.status_code == 200
    assert list(body["missing"]) == ["seller_tax_id"]
    assert body["duplicate"] is False and body["warnings"] == {}
    assert response.headers["cache-control"] == "no-store"
    assert flow_api.paths()[-3:] == [
        "/api/v1/invoices/check-required",
        "/api/v1/invoices/check-warnings",
        "/api/v1/invoices/check-duplicate",
    ]


def test_live_check_reports_api_failure_as_an_error_not_as_passed(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    import httpx

    _upload(admin_client, draft)
    flow_api.routes["/api/v1/invoices/check-required"] = httpx.Response(
        500, json={"error": {"code": "X", "message": "boom"}}
    )

    response = admin_client.post(
        f"/invoices/{draft}/flow/check", headers=_ORIGIN, data=review_data("check")
    )

    assert response.status_code == 502
    assert "error" in response.json() and "missing" not in response.json()


def test_live_check_requires_same_origin_and_does_not_call_the_api(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)
    calls = len(flow_api.requests)

    response = admin_client.post(f"/invoices/{draft}/flow/check", data=review_data("check"))

    assert response.status_code == 403 and len(flow_api.requests) == calls


def test_live_check_is_refused_after_submission(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)
    admin_client.post(f"/invoices/{draft}/flow/review", headers=_ORIGIN, data=review_data("submit"))

    response = admin_client.post(
        f"/invoices/{draft}/flow/check", headers=_ORIGIN, data=review_data("check")
    )

    assert response.status_code == 409


# ---- 國字大寫自動建議值（辨識當下伺服器端填一次；之後每次渲染重算提醒、不再填值）----
SUGGESTED = "參仟壹佰伍拾元整"  # RECOGNITION 的總計為 3150


def _recognition(zh: str | None, warnings: list[dict[str, str]] | None = None) -> httpx.Response:
    body = copy.deepcopy(RECOGNITION)
    body["invoice"]["total_amount_zh"] = zh
    body["warnings"] = warnings or []
    return httpx.Response(200, json=body)


UNREADABLE = {
    "field": "total_amount_zh",
    "code": "total_amount_zh_unreadable",
    "message": "無法判讀",
}
MISMATCH = {"field": "total_amount_zh", "code": "total_amount_zh_mismatch", "message": "不符"}


def _zh_input_value(html: str) -> str:
    import re

    match = re.search(r'<input id="f-total_amount_zh" name="total_amount_zh" value="([^"]*)"', html)
    assert match
    return match.group(1)


def test_blank_zh_is_filled_once_with_a_note_that_survives_rerendering(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)  # RECOGNITION 的國字為 None
    first = admin_client.get(f"/invoices/{draft}/flow?step=3").text
    second = admin_client.get(f"/invoices/{draft}/flow?step=3").text
    assert _zh_input_value(first) == SUGGESTED == _zh_input_value(second)
    assert "AI 沒有辨識到國字大寫金額" in first and "field-suggestion" in first
    assert (
        first.count("field-suggestion") == second.count("field-suggestion") == 1
    )  # 提醒只有一則，重開頁面不重複
    assert flow_api.paths() == [
        "/api/v1/invoices/recognize"
    ]  # 重新開啟頁面不再呼叫 API，也不重複填值


def test_unreadable_zh_is_replaced_and_note_shows_the_escaped_original(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    flow_api.routes["/api/v1/invoices/recognize"] = _recognition("<b>約壹萬</b>", [UNREADABLE])
    _upload(admin_client, draft)
    html = admin_client.get(f"/invoices/{draft}/flow?step=3").text
    assert _zh_input_value(html) == SUGGESTED
    assert "&lt;b&gt;約壹萬&lt;/b&gt;" in html and "<b>約壹萬</b>" not in html  # 原值已跳脫
    assert "無法判讀，已依總計自動帶入" in html


def test_check_keeps_the_suggestion_and_note_but_a_manual_value_is_never_overwritten(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)
    kept = admin_client.post(
        f"/invoices/{draft}/flow/review",
        headers=_ORIGIN,
        data=review_data("check", recognition_id=RID, total_amount_zh=SUGGESTED),
    ).text
    assert _zh_input_value(kept) == SUGGESTED and "field-suggestion" in kept  # 仍是建議值：提醒保留
    manual = admin_client.post(
        f"/invoices/{draft}/flow/review",
        headers=_ORIGIN,
        data=review_data("check", recognition_id=RID, total_amount_zh="自行填寫"),
    ).text
    assert _zh_input_value(manual) == "自行填寫" and "field-suggestion" not in manual
    again = admin_client.get(f"/invoices/{draft}/flow?step=3").text
    assert _zh_input_value(again) == "自行填寫"  # 之後再開也不被建議值覆寫
    cleared = admin_client.post(
        f"/invoices/{draft}/flow/review",
        headers=_ORIGIN,
        data=review_data("check", recognition_id=RID, total_amount_zh=""),
    ).text
    assert (
        _zh_input_value(cleared) == ""
    )  # 清空也不由伺服器補回（自動更新是瀏覽器端改總計時才發生）


def test_mismatch_and_tolerant_zh_are_left_alone(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    flow_api.routes["/api/v1/invoices/recognize"] = _recognition(
        "壹萬肆仟捌佰伍拾玖元整", [MISMATCH]
    )
    _upload(admin_client, draft)
    html = admin_client.get(f"/invoices/{draft}/flow?step=3").text
    assert _zh_input_value(html) == "壹萬肆仟捌佰伍拾玖元整" and "field-suggestion" not in html
