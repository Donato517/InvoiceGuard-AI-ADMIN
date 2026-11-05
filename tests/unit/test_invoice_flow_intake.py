# ruff: noqa: F811
"""測試目標：發票作業流程步驟 1～4 串接 API 辨識（Document AI OCR → 模型整理）、檢核與提交。

API 以 httpx.MockTransport 的假 API 替身（沿用 test_intake_routes）；合成資料，不連 API、DB 或 Storage。
"""

import copy
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from test_intake_routes import (  # noqa: F401
    INV,
    RECOGNITION,
    RID,
    SUBMITTED,
    FakeApi,
    api,
    review_data,
)

from app.routers import invoices as invoices_router
from app.schemas.e_admin import Action

_ORIGIN = {"Origin": "http://testserver"}  # 流程頁的上傳／核對 POST 需同源

ME = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
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


# ---- 重新上傳（系統已有相同發票時，放棄這次、改傳另一張新發票）----
# 業務定義：重新上傳 = 進入新發票上傳流程（GET /invoices/new → 全新草稿 → 步驟 1 選檔）。
# 不重設、不重新辨識同一份草稿，不修改或刪除已存在的重複發票，不清除其他草稿或案件；
# 准入規則沿用整條上傳流程（登入＋inv.upload VIEW），不要求 inv.intake CREATE。
# 下列測試走真正的 require_invoice_access 與路由，只替身部門模組與 API；結果是「假 API／假部門模組」下的離線驗證，
# 不是正式環境或真實瀏覽器的驗收。

DUPLICATE_409 = {
    "error": {"code": "INVOICE_DUPLICATED", "message": "此發票已存在於系統中，禁止重複上傳"}
}


class _Module:
    """假 E 模組：只允許 inv.upload VIEW；其餘功能（含 inv.intake CREATE）一律拒絕，證明流程不依賴它。"""

    access = object()

    def __init__(self, user_id: str, *, expired: bool = False) -> None:
        self.user_id = user_id
        self.expired = expired
        self.checks: list[tuple[str, Any]] = []

    def identity(self, token: Any) -> SimpleNamespace:
        if token is None or self.expired:
            from app.schemas.e_admin import UnauthenticatedError

            raise UnauthenticatedError
        return SimpleNamespace(user_id=self.user_id, selected_department_id=self.user_id)

    def check(self, identity: Any, code: str, action: Any, target: Any) -> None:
        from app.schemas.e_admin import DeniedError

        self.checks.append((code, action))
        if code != "inv.upload" or action != Action.VIEW:
            raise DeniedError

    def departments(self, identity: Any) -> tuple[Any, ...]:
        from app.schemas.e_access import EffectiveDepartment

        return (EffectiveDepartment(self.user_id, "t", "採購部"),)

    def sidebar_menu(self, identity: Any) -> tuple[()]:
        return ()


@pytest.fixture
def browser_as(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """以某員工登入的瀏覽器（真正的登入與權限檢查，只替身部門模組與 API）。"""
    from app.constants import SESSION_COOKIE_NAME
    from app.main import app
    from app.services.e_admin_service import get_e_module

    monkeypatch.setattr(invoices_router, "get_intake_client", lambda request: api.client())

    def build(
        user_id: str, *, cookie: bool = True, expired: bool = False
    ) -> tuple[TestClient, _Module]:
        module = _Module(user_id, expired=expired)
        app.dependency_overrides.clear()
        app.dependency_overrides[get_e_module] = lambda: module
        client = TestClient(app, follow_redirects=False)
        if cookie:
            client.cookies.set(SESSION_COOKIE_NAME, "synthetic-session")
        return client, module

    yield build
    app.dependency_overrides.clear()


def _start_draft(client: TestClient) -> str:
    response = client.get("/invoices/new")
    assert response.status_code == 303
    return response.headers["location"].split("/")[2]


def _duplicate_submit(client: TestClient, api: FakeApi, draft_id: str) -> Any:
    _upload(client, draft_id)
    api.routes["/api/v1/invoices"] = httpx.Response(409, json=DUPLICATE_409)
    return client.post(
        f"/invoices/{draft_id}/flow/review",
        headers=_ORIGIN,
        data=review_data("submit", recognition_id=RID),
    )


def test_duplicate_submit_shows_the_message_and_a_reupload_link_to_the_new_upload_flow(
    browser_as: Any, api: FakeApi
) -> None:
    client, _ = browser_as(ME)
    draft_id = _start_draft(client)

    response = _duplicate_submit(client, api, draft_id)

    assert response.status_code == 200  # 沒有 403、沒有導向登入頁
    assert "此發票已存在於系統中" in response.text  # 既有的重複發票提示（API 拒絕後的橫幅）
    assert 'href="/invoices/new"' in response.text and "data-reupload" in response.text
    assert "/flow/restart" not in response.text and 'href="/intake"' not in response.text
    assert "/cases/" not in response.text  # 沒有建立案件，也不連到既有案件


def test_check_reports_duplicate_and_page_still_offers_the_reupload_link(
    browser_as: Any, api: FakeApi
) -> None:
    client, _ = browser_as(ME)
    draft_id = _start_draft(client)
    _upload(client, draft_id)
    api.routes["/api/v1/invoices/check-duplicate"] = httpx.Response(
        200, json={"is_duplicate": True}
    )

    live = client.post(
        f"/invoices/{draft_id}/flow/check", headers=_ORIGIN, data=review_data("check")
    )
    paged = client.post(
        f"/invoices/{draft_id}/flow/review", headers=_ORIGIN, data=review_data("check")
    )

    assert live.status_code == 200 and live.json()["duplicate"] is True
    assert "系統內已有相同字軌、號碼與賣方統編的發票" in paged.text  # 整頁檢核摘要的查重提示
    assert 'href="/invoices/new"' in paged.text


def test_reupload_opens_a_new_draft_at_the_file_step_and_keeps_the_login(
    browser_as: Any, api: FakeApi
) -> None:
    client, module = browser_as(ME)
    first = _start_draft(client)
    _duplicate_submit(client, api, first)
    cookies_before = dict(client.cookies)
    calls = len(api.requests)

    link = client.get("/invoices/new")  # 使用者按下「重新上傳」

    assert link.status_code == 303 and "set-cookie" not in link.headers
    second = link.headers["location"].split("/")[2]
    assert link.headers["location"] == f"/invoices/{second}/flow?step=1"
    assert second != first  # 新草稿、新 id
    page = client.get(link.headers["location"])
    assert page.status_code == 200 and 'type="file"' in page.text  # 回到選檔步驟，仍登入
    assert dict(client.cookies) == cookies_before
    assert len(api.requests) == calls  # 按下重新上傳不呼叫任何 API
    assert not [c for c in module.checks if c[0] == "inv.intake"]  # 沒有要求 inv.intake


def test_a_new_image_can_be_uploaded_after_reupload_without_touching_the_duplicate_draft(
    browser_as: Any, api: FakeApi
) -> None:
    client, _ = browser_as(ME)
    first = _start_draft(client)
    _duplicate_submit(client, api, first)
    first_draft = invoices_router._find_flow_invoice(first)
    assert first_draft is not None
    before = copy.copy(first_draft)
    store_before = copy.deepcopy(invoices_router._INVOICE_STORE)
    api.routes["/api/v1/invoices"] = httpx.Response(201, json=SUBMITTED)
    paths_before = api.paths()

    second = client.get("/invoices/new").headers["location"].split("/")[2]
    upload = _upload(client, second)

    assert (
        upload.status_code == 303
        and upload.headers["location"] == f"/invoices/{second}/flow?step=3"
    )
    assert api.paths()[len(paths_before) :] == ["/api/v1/invoices/recognize"]  # 只有新影像的辨識
    assert 'value="辦公文具"' in client.get(f"/invoices/{second}/flow?step=3").text
    # 原本被拒絕的草稿：沒有被改寫、沒有被刪除、沒有變成案件；新影像沒有寫進去
    still = invoices_router._find_flow_invoice(first)
    assert still is first_draft and dict(still) == dict(before)
    assert not still.get("submitted_invoice_id")
    assert store_before == invoices_router._INVOICE_STORE  # 發票清單（案件）不變
    assert (
        "/api/v1/invoices" not in api.paths()[len(paths_before) :]
    )  # 沒有任何提交或修改案件的呼叫


def test_reupload_never_clears_or_overwrites_other_drafts_even_with_numeric_demo_ids(
    browser_as: Any,
) -> None:
    """單元測試環境的發票清單有數字 id 的示範資料；以前連續開草稿會撞號、後一份覆蓋前一份。"""
    assert any(str(i["id"]).isdigit() for i in invoices_router._INVOICE_STORE)
    mine, _ = browser_as(ME)
    mine_first = _start_draft(mine)
    other, _ = browser_as(OTHER)  # app 層級的登入替身一次只有一個身分；換人就重新建立
    other_id = _start_draft(other)
    invoices_router._DRAFT_INVOICE_STORE[other_id]["intake_form"] = "other-users-form"
    mine, _ = browser_as(ME)

    mine_second = _start_draft(mine)

    assert len({other_id, mine_first, mine_second}) == 3
    assert invoices_router._DRAFT_INVOICE_STORE[other_id]["intake_form"] == "other-users-form"
    assert invoices_router._DRAFT_INVOICE_STORE[other_id]["uploaded_by"] == OTHER
    assert invoices_router._DRAFT_INVOICE_STORE[mine_first]["uploaded_by"] == ME
    assert invoices_router._DRAFT_INVOICE_STORE[mine_second]["uploaded_by"] == ME


@pytest.mark.parametrize("state", ["no_cookie", "expired"])
def test_reupload_entry_requires_a_valid_login(browser_as: Any, state: str) -> None:
    client, _ = browser_as(ME, cookie=state != "no_cookie", expired=state == "expired")
    drafts_before = set(invoices_router._DRAFT_INVOICE_STORE)

    response = client.get("/invoices/new")

    assert response.status_code in (303, 401)
    if response.status_code == 303:
        assert response.headers["location"].startswith("/login")
    assert set(invoices_router._DRAFT_INVOICE_STORE) == drafts_before  # 沒有建立草稿


def test_reupload_flow_never_forbids_a_logged_in_employee_without_intake_create(
    browser_as: Any, api: FakeApi
) -> None:
    client, module = browser_as(ME)
    draft_id = _start_draft(client)

    statuses = [
        client.get("/invoices/new").status_code,
        client.get(f"/invoices/{draft_id}/flow?step=1").status_code,
        _upload(client, draft_id).status_code,
        client.get(f"/invoices/{draft_id}/flow?step=3").status_code,
    ]

    assert statuses == [303, 200, 303, 200]  # 沒有任何 403
    assert client.get("/intake").status_code == 403  # /intake 本身的權限不變
    assert ("inv.upload", Action.VIEW) in module.checks


def test_restart_route_no_longer_exists(browser_as: Any) -> None:
    client, _ = browser_as(ME)
    draft_id = _start_draft(client)

    response = client.post(f"/invoices/{draft_id}/flow/restart", headers=_ORIGIN)

    assert response.status_code in (404, 405)  # 不再有清除草稿的 POST 路由
