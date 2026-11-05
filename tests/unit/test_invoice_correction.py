"""測試目標：採購在 ADMIN 補正被財務退回的案件（return-items → 補正 → resubmit／void），全部離線。

分類：這裡全是單元測試——以 httpx.MockTransport 替身 API，不連資料庫、不連網路。
它證明 ADMIN 這一側「轉送、顯示、不複製規則」的行為；API 端的白名單、輪次與冪等規則是否真的拒絕，
要由 API 專案的隔離資料庫整合測試與雲端實測證明，本檔不聲稱已驗證。
"""

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.integrations.api_intake_client import ApiIntakeClient, BearerCredentials
from app.routers import invoice_correction as correction_router
from app.routers import invoices as inv
from app.schemas.intake import IntakeApiError, IntakeTransportError
from app.schemas.invoice_correction import CorrectionSubmit, CorrectionVoid
from app.services import invoice_correction_service as service

INVOICE_ID = "5b0f8b0e-6c7a-4c55-9a1e-0a1b2c3d4e5f"
ITEM_ID = "0a0a0a0a-1111-4222-8333-444455556666"
RETURN_ID = "9c9c9c9c-1111-4222-8333-444455556666"
OTHER_ID = "7d7d7d7d-1111-4222-8333-444455556666"
KEY = "11111111-2222-4333-8444-555555555555"
APPLICANT = "00000000-0000-4000-8000-0000000000a1"
STRANGER = "00000000-0000-4000-8000-0000000000b2"
ORIGIN = {"Origin": "http://testserver"}
TOKEN = "tok-secret-applicant"
API = "http://127.0.0.1:8100"


def _ref(scope: str, name: str, item: str | None = None, line: int | None = None) -> dict[str, Any]:
    return {"field_scope": scope, "line_no": line, "item_id": item, "field_name": name}


def return_items_json(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "invoice_id": INVOICE_ID,
        "status": "returned_to_applicant",
        "submission_round": 2,
        "can_resubmit": True,
        "requires_void": False,
        "resubmit_blocked_reason": None,
        "editable_fields": [
            _ref("header", "total_amount"),
            _ref("item", "line_amount", ITEM_ID, 1),
        ],
        "return_items": [
            {
                "return_item_id": RETURN_ID,
                "item_kind": "field",
                "target": _ref("header", "total_amount"),
                "reason": "總計與明細不符",
                "allowed_fields": [_ref("item", "line_amount", ITEM_ID, 1)],
                "status": "open",
            }
        ],
        "previous": {
            "invoice_track": "RT",
            "invoice_number": "39560800",
            "invoice_date": "2026-10-20",
            "seller_tax_id": "43149447",
            "seller_name": "賣方公司",
            "buyer_tax_id": "12345678",
            "buyer_name": "買方公司",
            "tax_type": "taxable",
            "sales_amount": "1000",
            "tax_amount": "50",
            "total_amount": "1050",
            "total_amount_zh": "壹仟零伍拾元整",
            "items": [
                {
                    "item_id": ITEM_ID,
                    "line_no": 1,
                    "item_name": "文具",
                    "quantity": "2",
                    "unit_price": "500",
                    "line_amount": "1000",
                }
            ],
        },
    }
    body.update(overrides)
    return body


def resubmit_json(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "invoice_id": INVOICE_ID,
        "status": "pending_accounting_review",
        "submission_round": 3,
        "submitted_at": "2026-11-05T01:00:00Z",
        "tax_period": "11510",
        "warnings": [{"field": "invoice_date", "code": "FUTURE", "message": "日期提醒"}],
        "modified_fields": [{"field_scope": "header", "field_name": "total_amount"}],
        "rereview_fields": [_ref("header", "total_amount")],
        "replayed": False,
    }
    body.update(overrides)
    return body


def error_json(code: str, message: str = "上游訊息") -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": []}}


class FakeApi:
    """替身 API：記錄每個請求，依 (方法, 路徑尾段) 回應；可注入例外模擬逾時。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responses: dict[str, Callable[[httpx.Request], httpx.Response] | httpx.Response] = {}
        self.raises: dict[str, Exception] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        tail = request.url.path.rsplit("/", 1)[-1]
        if tail in self.raises:
            raise self.raises[tail]
        reply = self.responses[tail]
        return reply(request) if callable(reply) else reply

    def client(self, token: str = TOKEN) -> ApiIntakeClient:
        return ApiIntakeClient(
            API, BearerCredentials(token), transport=httpx.MockTransport(self.handler)
        )

    def bodies(self, tail: str) -> list[dict[str, Any]]:
        return [
            json.loads(r.content) for r in self.requests if r.url.path.endswith(tail) and r.content
        ]

    def paths(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]


@pytest.fixture
def api() -> FakeApi:
    fake = FakeApi()
    fake.responses["return-items"] = httpx.Response(200, json=return_items_json())
    # API 的首次成功回 201（相同冪等鍵的重試才回 200 與 replayed=true）
    fake.responses["resubmit"] = httpx.Response(201, json=resubmit_json())
    fake.responses["void"] = httpx.Response(
        200,
        json={
            "invoice_id": INVOICE_ID,
            "status": "voided",
            "submission_round": 2,
            "voided_at": "2026-11-05T01:00:00Z",
        },
    )
    return fake


# ---------------------------------------------------------------- 單一 API client（MockTransport）


def test_client_reads_return_items_with_the_users_own_bearer_token(api: FakeApi) -> None:
    info = api.client().get_return_items(INVOICE_ID)

    request = api.requests[0]
    assert (request.method, request.url.path) == (
        "GET",
        f"/api/v1/invoices/{INVOICE_ID}/return-items",
    )
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert info.submission_round == 2 and info.can_resubmit is True
    assert info.editable_fields[0].field_name == "total_amount"
    assert str(info.previous.items[0].item_id) == ITEM_ID


def test_client_rejects_a_non_uuid_id_before_building_the_path(api: FakeApi) -> None:
    with pytest.raises(IntakeApiError) as raised:
        api.client().get_return_items("../../auth/login")

    assert raised.value.status == 404 and api.requests == []  # 使用者輸入不會拼進 API 路徑


def test_client_resubmit_and_void_post_to_the_api_paths(api: FakeApi) -> None:
    client = api.client()

    client.resubmit(INVOICE_ID, {"expected_round": 2})
    client.void(INVOICE_ID, {"expected_round": 2})

    assert api.paths() == [
        f"POST /api/v1/invoices/{INVOICE_ID}/resubmit",
        f"POST /api/v1/invoices/{INVOICE_ID}/void",
    ]


@pytest.mark.parametrize(("status", "replayed"), [(201, False), (200, True)])
def test_client_treats_both_resubmit_success_statuses_as_success(
    api: FakeApi, status: int, replayed: bool
) -> None:
    """API 首次成功回 201、冪等重試回 200；兩者都不能被當成異常。"""
    api.responses["resubmit"] = httpx.Response(status, json=resubmit_json(replayed=replayed))

    result = api.client().resubmit(INVOICE_ID, {"expected_round": 2})

    assert result.replayed is replayed and result.submission_round == 3


def test_client_still_rejects_other_resubmit_statuses(api: FakeApi) -> None:
    api.responses["resubmit"] = httpx.Response(202, json=resubmit_json())

    with pytest.raises(IntakeApiError):
        api.client().resubmit(INVOICE_ID, {"expected_round": 2})


def test_first_successful_resubmit_201_is_success_in_the_browser_response(
    browser: TestClient, api: FakeApi
) -> None:
    """回歸：隔離整合測試發現 API 首次成功回 201 時，ADMIN 曾誤回 UPSTREAM_ERROR。"""
    api.responses["resubmit"] = httpx.Response(201, json=resubmit_json())

    response = browser.post(f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=_submit_body())

    assert response.status_code == 200
    data = response.json()
    assert "error" not in data and data["submission_round"] == 3 and data["replayed"] is False


def test_timeout_after_sending_a_write_is_ambiguous_but_a_read_is_not(api: FakeApi) -> None:
    api.raises["resubmit"] = httpx.ReadTimeout("t")
    api.raises["void"] = httpx.ReadTimeout("t")
    api.raises["return-items"] = httpx.ReadTimeout("t")
    client = api.client()

    with pytest.raises(IntakeTransportError) as resubmit:
        client.resubmit(INVOICE_ID, {})
    with pytest.raises(IntakeTransportError) as void:
        client.void(INVOICE_ID, {})
    with pytest.raises(IntakeTransportError) as read:
        client.get_return_items(INVOICE_ID)

    assert resubmit.value.ambiguous is True  # 可能已處理，不能當成失敗換鍵重送
    assert void.value.ambiguous is True
    assert read.value.ambiguous is False  # 只讀可安全重試


def test_a_malformed_2xx_resubmit_response_is_ambiguous(api: FakeApi) -> None:
    api.responses["resubmit"] = httpx.Response(200, json={"unexpected": True})

    with pytest.raises(IntakeTransportError) as raised:
        api.client().resubmit(INVOICE_ID, {})

    assert raised.value.ambiguous is True


def test_error_messages_are_chinese_and_never_echo_upstream_for_auth_or_5xx() -> None:
    def err(status: int, code: str) -> IntakeApiError:
        return IntakeApiError(status, code, "UPSTREAM-SECRET-DETAIL")

    assert "財務沒有指定" in service.error_message(err(400, "RESUBMIT_FIELD_NOT_ALLOWED"))
    assert "較新的輪次" in service.error_message(err(409, "RESUBMIT_ROUND_STALE"))
    assert "圖片問題" in service.error_message(err(409, "RESUBMIT_IMAGE_REQUIRES_VOID"))
    assert "不能新增、刪除或重排" in service.error_message(
        err(400, "RESUBMIT_ITEMS_STRUCTURE_CHANGED")
    )
    assert "重新登入" in service.error_message(err(401, "UNAUTHENTICATED"))
    assert "SECRET" not in service.error_message(err(503, "X"))
    assert "原提交者" in service.error_message(err(404, "NOT_FOUND"))


# ---------------------------------------------------------------- 傳輸層 schema（不複製 API 規則）


def _submit_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "idempotency_key": KEY,
        "expected_round": 2,
        "invoice_track": "RT",
        "invoice_number": "39560800",
        "invoice_date": "2026-10-20",
        "seller_tax_id": "43149447",
        "seller_name": "賣方公司",
        "buyer_tax_id": "12345678",
        "buyer_name": "買方公司",
        "tax_type": "taxable",
        "sales_amount": "1000",
        "tax_amount": "50",
        "total_amount": "1050.50",
        "total_amount_zh": None,
        "items": [
            {
                "item_id": ITEM_ID,
                "item_name": "文具",
                "quantity": "2",
                "unit_price": "500",
                "line_amount": "1000",
            }
        ],
        "changes": [
            {
                "field_scope": "header",
                "line_no": None,
                "field_name": "total_amount",
                "old_value": "1050",
                "new_value": "1050.50",
            }
        ],
        "responses": [{"return_item_id": RETURN_ID, "action": "corrected", "note": None}],
    }
    body.update(overrides)
    return body


def test_submit_schema_keeps_amounts_as_exact_strings() -> None:
    parsed = CorrectionSubmit.model_validate(_submit_body())

    assert parsed.model_dump(mode="json")["total_amount"] == "1050.50"


def test_submit_schema_rejects_float_amounts_unknown_keys_and_self_declared_identity() -> None:
    with pytest.raises(ValidationError):
        CorrectionSubmit.model_validate(_submit_body(total_amount=1050.5))
    with pytest.raises(ValidationError):
        CorrectionSubmit.model_validate(_submit_body(user_id=APPLICANT))
    with pytest.raises(ValidationError):
        CorrectionSubmit.model_validate(_submit_body(responses=[]))
    with pytest.raises(ValidationError):
        CorrectionVoid.model_validate({"expected_round": 1.5})


# ---------------------------------------------------------------- 頁面與路由


class Env:
    def __init__(self, api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
        self.api = api
        self.token_missing = False
        self.invoice: dict[str, Any] | None = {
            "id": INVOICE_ID,
            "uploaded_by": APPLICANT,
            "file_name": "invoice.jpg",
            "file_media_type": "image/jpeg",
        }
        monkeypatch.setattr(
            correction_router.invoice_review_service, "fetch_invoice", self._fetch_invoice
        )
        monkeypatch.setattr(correction_router, "get_intake_client", self._client)
        monkeypatch.setattr(
            inv, "get_intake_client", self._client
        )  # 舊的 /procurement/terminate 路由

    def _fetch_invoice(self, invoice_id: Any) -> dict[str, Any] | None:
        return self.invoice if str(invoice_id) == INVOICE_ID else None

    def _client(self, request: Request) -> ApiIntakeClient:
        if self.token_missing:
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="請重新登入後再試")
        return self.api.client()


@pytest.fixture
def env(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> Env:
    return Env(api, monkeypatch)


def _as(user_id: str, admin_client: TestClient) -> TestClient:
    from app.core.deps import require_admin
    from app.main import app

    def dependency(request: Request) -> SimpleNamespace:
        admin = SimpleNamespace(id=user_id, display_name="申請人", role="admin", is_active=True)
        request.state.admin = admin
        return admin

    app.dependency_overrides[require_admin] = dependency
    return admin_client


@pytest.fixture
def browser(admin_client: TestClient, env: Env) -> TestClient:
    return _as(APPLICANT, admin_client)


def _page(browser: TestClient, invoice_id: str = INVOICE_ID) -> Any:
    return browser.get(f"/invoices/{invoice_id}/correct")


def _input_tag(html: str, marker: str) -> str:
    start = html.index(marker)
    return html[html.rindex("<", 0, start) : html.index(">", start) + 1]


def test_correct_page_stays_in_admin_and_only_the_api_allowed_fields_are_editable(
    browser: TestClient, api: FakeApi
) -> None:
    response = _page(browser)
    html = response.text

    assert response.status_code == 200
    assert api.paths() == [f"GET /api/v1/invoices/{INVOICE_ID}/return-items"]
    assert "總計與明細不符" in html and "財務退回原因" in html
    assert "InvoiceGuard-AI" not in html and "vercel.app" not in html  # 沒有導向前台
    # editable_fields 只有 total_amount 與第 1 列 line_amount
    assert "readonly" not in _input_tag(html, 'id="f-total_amount"')
    assert "readonly" in _input_tag(html, 'id="f-invoice_number"')
    assert "readonly" in _input_tag(html, 'id="f-seller_tax_id"')
    assert "disabled" in _input_tag(html, 'id="f-tax_type"')
    assert "readonly" in _input_tag(html, 'aria-label="品名 第 1 列"')
    assert "readonly" not in _input_tag(html, 'aria-label="金額 第 1 列"')
    assert 'value="1050"' in _input_tag(html, 'id="f-total_amount"')  # 上一輪提交值，不是 AI 原值
    assert "add_item" not in html and "remove_item" not in html  # 沒有新增／刪除明細的控制項
    assert html.count("data-item-id=") >= 1 and "<button" in html and "新增一列" not in html
    assert "/invoices/" + INVOICE_ID + "/file" in html  # 沿用原圖窗格


def test_page_hides_the_retry_button_with_scoped_css_and_scrolls_the_item_table(
    browser: TestClient,
) -> None:
    """回歸：真實瀏覽器發現 .btn 的 display 蓋過 hidden（重試按鈕一直顯示）、明細表被欄位容器截斷。

    這裡只檢查樣板與 CSS 的結構；實際是否可見／可捲動要用真實瀏覽器驗證（isolated_e2e/browser_scenarios.py）。
    """
    html = _page(browser).text
    css = browser.get("/static/css/pages/invoice-correct.css")

    assert "css/pages/invoice-correct.css" in html
    retry = _input_tag(html, 'id="correct-retry"')
    assert " hidden" in retry and "btn" in retry  # 初始帶 hidden，靠本頁 CSS 才真的隱藏
    assert css.status_code == 200 and "#correct-page .btn[hidden]" in css.text
    assert "display: none" in css.text
    # 規則只限定在補正頁容器內，不是全站 .btn[hidden]
    selectors = [
        line.split("{")[0].strip()
        for line in css.text.splitlines()
        if "{" in line and not line.strip().startswith("/*")
    ]
    assert selectors and all(sel.startswith("#correct-page") for sel in selectors)
    # 明細表包在全站既有的 .table-wrap（overflow: auto）裡，可局部水平捲動
    wrap_at = html.index('class="table-wrap"')
    assert html.index('<table class="table">') > wrap_at
    assert 'aria-label="明細（可左右捲動）"' in html and html.count("<table") == 1
    assert "overflow: auto" in browser.get("/static/css/components.css").text


def test_a_different_user_cannot_open_or_submit_the_case(
    admin_client: TestClient, env: Env, api: FakeApi
) -> None:
    other = _as(STRANGER, admin_client)

    page = _page(other)
    submit = other.post(f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=_submit_body())
    void = other.post(
        f"/invoices/{INVOICE_ID}/correct/void", headers=ORIGIN, json={"expected_round": 2}
    )

    assert (page.status_code, submit.status_code, void.status_code) == (404, 404, 404)
    assert api.requests == []  # 連 API 都沒有呼叫


def test_demo_drafts_and_malformed_ids_never_reach_the_correction_api(
    browser: TestClient, env: Env, api: FakeApi
) -> None:
    demo = _page(browser, "1")
    bad = _page(browser, "not-a-uuid")
    env.invoice = None
    missing = _page(browser)

    assert (demo.status_code, bad.status_code, missing.status_code) == (404, 404, 404)
    assert api.requests == []


def test_missing_api_token_asks_for_login_instead_of_falling_back(
    browser: TestClient, env: Env, api: FakeApi
) -> None:
    env.token_missing = True

    submit = browser.post(f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=_submit_body())

    assert submit.status_code == 401 and "重新登入" in submit.json()["error"]["message"]
    assert api.requests == []


def test_api_404_and_not_returned_are_reported_without_a_form(
    browser: TestClient, api: FakeApi
) -> None:
    api.responses["return-items"] = httpx.Response(409, json=error_json("INVOICE_NOT_RETURNED"))

    response = _page(browser)

    assert response.status_code == 409 and "已不是待補正狀態" in response.text
    assert "correct-form" not in response.text

    api.responses["return-items"] = httpx.Response(404, json=error_json("NOT_FOUND"))
    assert _page(browser).status_code == 404


def test_image_return_shows_no_field_form_and_offers_only_void(
    browser: TestClient, api: FakeApi
) -> None:
    api.responses["return-items"] = httpx.Response(
        200,
        json=return_items_json(
            can_resubmit=False,
            requires_void=True,
            editable_fields=[],
            return_items=[
                {
                    "return_item_id": OTHER_ID,
                    "item_kind": "image",
                    "target": None,
                    "reason": "影像模糊",
                    "allowed_fields": [],
                    "status": "open",
                }
            ],
        ),
    )

    html = _page(browser).text

    assert "影像模糊" in html and "終止並重新上傳" in html
    assert "correct-form" not in html and "correct-submit" not in html
    assert "不能直接換圖重送" in html and "終止成功不代表新的發票已建立" in html


def test_a_case_the_api_says_cannot_resubmit_is_shown_read_only(
    browser: TestClient, api: FakeApi
) -> None:
    api.responses["return-items"] = httpx.Response(
        200,
        json=return_items_json(
            can_resubmit=False,
            requires_void=False,
            resubmit_blocked_reason="沒有可補正的欄位",
            return_items=[
                {
                    "return_item_id": OTHER_ID,
                    "item_kind": "other",
                    "target": None,
                    "reason": "請補紙本",
                    "allowed_fields": [],
                    "status": "open",
                }
            ],
        ),
    )

    html = _page(browser).text

    assert "沒有可補正的欄位" in html and "請補紙本" in html
    assert "correct-form" not in html


def test_submit_forwards_the_complete_payload_with_the_same_idempotency_key(
    browser: TestClient, api: FakeApi
) -> None:
    response = browser.post(f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=_submit_body())

    assert response.status_code == 200  # API 回 201，ADMIN 視為成功
    data = response.json()
    assert data["status"] == "pending_accounting_review" and data["submission_round"] == 3
    assert data["status_label"] and data["replayed"] is False
    sent = api.bodies("/resubmit")[0]
    assert sent["idempotency_key"] == KEY and sent["expected_round"] == 2
    assert sent["total_amount"] == "1050.50"  # 字串，沒有經過 float
    assert sent["changes"][0]["new_value"] == "1050.50"
    assert api.requests[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert "user_id" not in sent and "department_id" not in sent


def test_retrying_the_same_key_after_a_timeout_replays_without_a_new_round(
    browser: TestClient, api: FakeApi
) -> None:
    api.raises["resubmit"] = httpx.ReadTimeout("t")
    first = browser.post(f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=_submit_body())
    assert first.status_code == 502 and first.json()["ambiguous"] is True

    del api.raises["resubmit"]
    api.responses["resubmit"] = httpx.Response(200, json=resubmit_json(replayed=True))
    again = browser.post(f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=_submit_body())

    keys = [b["idempotency_key"] for b in api.bodies("/resubmit")]
    assert keys == [KEY, KEY]  # ADMIN 不會替瀏覽器換新的鍵
    assert again.json()["replayed"] is True and again.json()["submission_round"] == 3


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (400, "RESUBMIT_FIELD_NOT_ALLOWED", "財務沒有指定的欄位"),
        (409, "RESUBMIT_ROUND_STALE", "較新的輪次"),
        (409, "RESUBMIT_IMAGE_REQUIRES_VOID", "圖片問題"),
        (400, "RESUBMIT_RESPONSES_INCOMPLETE", "逐項回覆"),
        (400, "RESUBMIT_ITEMS_STRUCTURE_CHANGED", "不能新增、刪除或重排"),
        (409, "INVOICE_DUPLICATED", "不能重複送出"),
        (409, "IDEMPOTENCY_KEY_REUSED", "內容已變更"),
    ],
)
def test_api_rejections_are_passed_back_as_chinese_messages(
    browser: TestClient, api: FakeApi, status: int, code: str, expected: str
) -> None:
    api.responses["resubmit"] = httpx.Response(status, json=error_json(code, "english upstream"))

    response = browser.post(f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=_submit_body())

    assert response.status_code == status
    assert expected in response.json()["error"]["message"]
    assert "english upstream" not in response.text


def test_unauthorized_field_edits_are_decided_by_the_api_not_by_admin(
    browser: TestClient, api: FakeApi
) -> None:
    """ADMIN 不自己判斷白名單：連未授權的欄位（seller_tax_id）也照樣轉送，由 API 拒絕。"""
    api.responses["resubmit"] = httpx.Response(400, json=error_json("RESUBMIT_FIELD_NOT_ALLOWED"))
    body = _submit_body(seller_tax_id="99999999")

    response = browser.post(f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=body)

    assert api.bodies("/resubmit")[0]["seller_tax_id"] == "99999999"
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "RESUBMIT_FIELD_NOT_ALLOWED"


def test_float_amounts_and_cross_site_posts_are_rejected_before_any_api_call(
    browser: TestClient, api: FakeApi
) -> None:
    float_body = browser.post(
        f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=_submit_body(total_amount=1050.5)
    )
    no_origin = browser.post(f"/invoices/{INVOICE_ID}/correct", json=_submit_body())

    assert float_body.status_code in (400, 422)  # 全域驗證錯誤處理器回 400
    assert no_origin.status_code == 403
    assert api.requests == []


def test_void_calls_the_api_and_never_writes_the_database(
    browser: TestClient, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("新的終止流程不得直接寫資料庫")

    monkeypatch.setattr(inv.finance_db, "commit_change", forbidden)

    response = browser.post(
        f"/invoices/{INVOICE_ID}/correct/void",
        headers=ORIGIN,
        json={"expected_round": 2, "reason": "圖片模糊"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "voided" and data["upload_url"] == "/invoices/new"
    assert api.paths() == [f"POST /api/v1/invoices/{INVOICE_ID}/void"]
    assert api.bodies("/void")[0] == {"expected_round": 2, "reason": "圖片模糊"}


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (409, "INVOICE_NOT_RETURNED", "已不是待補正狀態"),
        (409, "RESUBMIT_ROUND_STALE", "較新的輪次"),
        (404, "NOT_FOUND", "原提交者"),
    ],
)
def test_repeated_or_stale_void_is_reported(
    browser: TestClient, api: FakeApi, status: int, code: str, expected: str
) -> None:
    api.responses["void"] = httpx.Response(status, json=error_json(code))

    response = browser.post(
        f"/invoices/{INVOICE_ID}/correct/void", headers=ORIGIN, json={"expected_round": 2}
    )

    assert response.status_code == status and expected in response.json()["error"]["message"]


def test_void_timeout_is_ambiguous_and_does_not_claim_success(
    browser: TestClient, api: FakeApi
) -> None:
    api.raises["void"] = httpx.ReadTimeout("t")

    response = browser.post(
        f"/invoices/{INVOICE_ID}/correct/void", headers=ORIGIN, json={"expected_round": 2}
    )

    assert response.status_code == 502 and response.json()["ambiguous"] is True
    assert "status" not in response.json()


# ---------------------------------------------------------------- 列表按鈕


def _db_row(status: str, owner: str = APPLICANT, invoice_id: str = INVOICE_ID) -> dict[str, Any]:
    row = dict(inv.DEMO_TEST_INVOICES[2])
    row.update(
        id=invoice_id,
        uploaded_by=owner,
        db_status=status,
        review_status="returned" if status.startswith("returned") else "pending",
        status="flagged",
        is_completed=False,
        stage=3,
    )
    return row


@pytest.fixture
def listing(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[dict[str, Any]]], None]:
    def install(rows: list[dict[str, Any]]) -> None:
        monkeypatch.setattr(
            inv.invoice_review_service, "fetch_invoices", lambda **kwargs: [dict(r) for r in rows]
        )

    return install


def test_list_shows_correct_only_for_my_returned_database_case(
    admin_client: TestClient, listing: Callable[[list[dict[str, Any]]], None]
) -> None:
    listing([_db_row("returned_to_applicant")])

    html = _as(APPLICANT, admin_client).get("/invoices").text

    assert f'href="/invoices/{INVOICE_ID}/correct"' in html
    assert ">補正</a>" in html and "審核流程 →" not in html


@pytest.mark.parametrize(
    "status", ["returned_to_accounting", "pending_accounting_review", "voided"]
)
def test_list_does_not_offer_correct_for_other_states(
    admin_client: TestClient, listing: Callable[[list[dict[str, Any]]], None], status: str
) -> None:
    listing([_db_row(status)])

    html = _as(APPLICANT, admin_client).get("/invoices").text

    assert "data-correct-action" not in html


def test_list_does_not_offer_correct_to_someone_else_or_for_demo_rows(
    admin_client: TestClient, listing: Callable[[list[dict[str, Any]]], None]
) -> None:
    demo = dict(inv.DEMO_TEST_INVOICES[0])
    demo.update(review_status="returned", status="flagged", is_completed=False, stage=3)
    listing([_db_row("returned_to_applicant", owner=STRANGER), demo])

    html = _as(APPLICANT, admin_client).get("/invoices").text

    assert "data-correct-action" not in html  # 非本人、沒有 db_status 的示範資料都不顯示


# ---------------------------------------------------------------- 補正頁補強：圖片退件、部門／身分由 API 決定


def test_image_return_cannot_use_the_normal_resubmit_the_api_refuses_it(
    browser: TestClient, api: FakeApi
) -> None:
    """即使繞過頁面直接 POST resubmit，ADMIN 也只是轉送；圖片退件由 API 回 409，且畫面不給一般送出入口。"""
    api.responses["resubmit"] = httpx.Response(409, json=error_json("RESUBMIT_IMAGE_REQUIRES_VOID"))

    response = browser.post(f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=_submit_body())

    assert response.status_code == 409
    assert "終止並重新上傳" in response.json()["error"]["message"]
    assert api.paths() == [f"POST /api/v1/invoices/{INVOICE_ID}/resubmit"]  # 沒有任何資料庫寫入路徑


def test_department_or_submitter_mismatch_is_the_apis_404_and_reaches_the_user_as_404(
    browser: TestClient, api: FakeApi
) -> None:
    """部門與原提交者的判斷屬於 API（get_own_invoice）；ADMIN 不自己推論，API 說 404 就是 404。"""
    api.responses["return-items"] = httpx.Response(404, json=error_json("NOT_FOUND"))
    api.responses["resubmit"] = httpx.Response(404, json=error_json("NOT_FOUND"))

    page = _page(browser)
    submit = browser.post(f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=_submit_body())

    assert page.status_code == 404
    assert submit.status_code == 404 and "原提交者" in submit.json()["error"]["message"]


def test_success_response_shows_the_state_the_api_returned(
    browser: TestClient, api: FakeApi
) -> None:
    api.responses["resubmit"] = httpx.Response(
        200, json=resubmit_json(status="pending_accounting_review", submission_round=4)
    )

    data = browser.post(
        f"/invoices/{INVOICE_ID}/correct", headers=ORIGIN, json=_submit_body()
    ).json()

    assert data["status"] == "pending_accounting_review"  # 由 API 決定，ADMIN 沒有寫死
    assert data["submission_round"] == 4 and data["tax_period"] == "11510"
    assert data["warnings"] == ["日期提醒"] and data["rereview_count"] == 1


# ---------------------------------------------------------------- 舊的 POST /procurement/terminate 不得繞過 API


def _legacy_terminate(client: TestClient, **data: Any) -> Any:
    return client.post(f"/invoices/{INVOICE_ID}/procurement/terminate", headers=ORIGIN, data=data)


@pytest.fixture
def no_db_write(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """舊路徑任何資料庫寫入或審核資料讀取都視為失敗。"""
    touched: list[str] = []

    def forbid(name: str) -> Callable[..., None]:
        def inner(*args: Any, **kwargs: Any) -> None:
            touched.append(name)
            raise AssertionError(f"舊終止路由不得直接使用 {name}")

        return inner

    monkeypatch.setattr(inv.finance_db, "commit_change", forbid("commit_change"))
    return touched


def test_legacy_terminate_of_a_database_case_goes_through_the_api_void(
    browser: TestClient, api: FakeApi, no_db_write: list[str]
) -> None:
    response = _legacy_terminate(browser, reason="供應商將重開發票", expected_round="2")

    assert response.status_code == 303 and response.headers["location"] == "/invoices"
    assert api.paths() == [f"POST /api/v1/invoices/{INVOICE_ID}/void"]
    assert api.bodies("/void") == [{"expected_round": 2, "reason": "供應商將重開發票"}]
    assert api.requests[0].headers["authorization"] == f"Bearer {TOKEN}"  # 原提交者自己的憑證
    assert no_db_write == []


def test_legacy_terminate_without_a_round_is_refused_and_calls_nothing(
    browser: TestClient, api: FakeApi, no_db_write: list[str]
) -> None:
    response = _legacy_terminate(browser, reason="x")

    assert response.status_code == 400 and "缺少案件輪次" in response.text
    assert api.requests == [] and no_db_write == []


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (409, "RESUBMIT_ROUND_STALE", "較新的輪次"),
        (409, "INVOICE_NOT_RETURNED", "已不是待補正狀態"),
        (503, "UNAVAILABLE", "暫時發生錯誤"),
    ],
)
def test_legacy_terminate_shows_the_apis_rules_and_never_falls_back_to_the_database(
    browser: TestClient,
    api: FakeApi,
    no_db_write: list[str],
    status: int,
    code: str,
    expected: str,
) -> None:
    api.responses["void"] = httpx.Response(status, json=error_json(code))

    response = _legacy_terminate(browser, reason="x", expected_round="2")

    assert expected in response.text
    assert response.status_code == (502 if status >= 500 else status)
    assert no_db_write == []


def test_legacy_terminate_by_someone_who_is_not_the_submitter_is_the_apis_404(
    browser: TestClient, api: FakeApi, no_db_write: list[str]
) -> None:
    api.responses["void"] = httpx.Response(404, json=error_json("NOT_FOUND"))

    response = _legacy_terminate(browser, reason="x", expected_round="2")

    assert response.status_code == 404 and no_db_write == []


def test_legacy_terminate_without_an_api_token_or_origin_does_nothing(
    browser: TestClient, env: Env, api: FakeApi, no_db_write: list[str]
) -> None:
    no_origin = browser.post(
        f"/invoices/{INVOICE_ID}/procurement/terminate", data={"reason": "x", "expected_round": "2"}
    )
    env.token_missing = True
    no_token = _legacy_terminate(browser, reason="x", expected_round="2")

    assert no_origin.status_code == 403 and no_token.status_code == 401
    assert api.requests == [] and no_db_write == []


def test_legacy_terminate_timeout_is_not_reported_as_success(
    browser: TestClient, api: FakeApi, no_db_write: list[str]
) -> None:
    api.raises["void"] = httpx.ReadTimeout("t")

    response = _legacy_terminate(browser, reason="x", expected_round="2")

    assert response.status_code == 502 and response.headers.get("location") is None
    assert no_db_write == []


# ---------------------------------------------------------------- 「補正」按鈕的條件


def test_finance_department_user_who_submitted_the_case_still_gets_the_correct_button(
    admin_client: TestClient, listing: Callable[[list[dict[str, Any]]], None]
) -> None:
    """admin_client 的測試身分在財務工作區：本人被退回的案件仍顯示「補正」，資格由 API 在進頁面時判斷。"""
    listing([_db_row("returned_to_applicant")])

    html = _as(APPLICANT, admin_client).get("/invoices").text

    assert "data-correct-action" in html and "data-self-upload-action" not in html


def test_the_label_alone_does_not_grant_the_button(
    admin_client: TestClient, listing: Callable[[list[dict[str, Any]]], None]
) -> None:
    """只有「待補正」顯示文字、沒有資料庫狀態 returned_to_applicant 的列（例如示範資料）不給按鈕。"""
    row = _db_row("returned_to_applicant")
    row.pop("db_status")
    listing([row])

    html = _as(APPLICANT, admin_client).get("/invoices").text

    assert "待補正" in html and "data-correct-action" not in html
