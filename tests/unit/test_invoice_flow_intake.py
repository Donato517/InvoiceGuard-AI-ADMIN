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


@pytest.fixture(autouse=True)
def _isolated_draft_store() -> Iterator[None]:
    """草稿字典是模組層級的記憶體；每個測試前後清空，避免前一個測試留下的草稿影響下一個。"""
    invoices_router._DRAFT_INVOICE_STORE.clear()
    yield
    invoices_router._DRAFT_INVOICE_STORE.clear()


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
# 業務定義（2026-11-05 使用者確認）：按下「重新上傳」→ 刪除本次尚未成功提交的原草稿（辨識結果、核對資料、暫存影像），
# 不刪除或修改資料庫中已存在的重複發票，不影響其他使用者或其他草稿，保留登入 Session，回到新發票上傳頁。
# 操作：POST /invoices/{id}/flow/discard（有副作用，所以不用 GET）→ 303 /invoices/new → 全新草稿的步驟 1。
# 准入：登入＋inv.upload VIEW（router 層）＋草稿上傳者本人＋同源；不要求 inv.intake CREATE。
# 下列測試走真正的 require_invoice_access 與路由，只替身部門模組與 API；是記憶體內、假 API 的離線驗證，
# 不是資料庫、Storage、真實瀏覽器或正式環境的驗收。

DUPLICATE_409 = {
    "error": {"code": "INVOICE_DUPLICATED", "message": "此發票已存在於系統中，禁止重複上傳"}
}


class _Module:
    """假 E 模組：只允許 inv.upload VIEW；其餘功能（含 inv.intake CREATE）一律拒絕，證明流程不依賴它。"""

    access = object()

    def __init__(self, user_id: str, department: str = "採購部", *, expired: bool = False) -> None:
        self.user_id = user_id
        self.department = department
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

        return (EffectiveDepartment(self.user_id, "t", self.department),)

    def sidebar_menu(self, identity: Any) -> tuple[()]:
        return ()


@pytest.fixture
def browser_as(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """以某員工登入的瀏覽器（真正的登入與權限檢查，只替身部門模組與 API）；app 層級一次只有一個身分，換人就重新建立。"""
    from app.constants import SESSION_COOKIE_NAME
    from app.main import app
    from app.services.e_admin_service import get_e_module

    monkeypatch.setattr(invoices_router, "get_intake_client", lambda request: api.client())

    def build(
        user_id: str, department: str = "採購部", *, cookie: bool = True, expired: bool = False
    ) -> tuple[TestClient, _Module]:
        module = _Module(user_id, department, expired=expired)
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
    """上傳、辨識，再提交：API 回 409 重複發票（沒有建立任何案件）。"""
    _upload(client, draft_id)
    api.routes["/api/v1/invoices"] = httpx.Response(409, json=DUPLICATE_409)
    return client.post(
        f"/invoices/{draft_id}/flow/review",
        headers=_ORIGIN,
        data=review_data("submit", recognition_id=RID),
    )


def _discard(client: TestClient, draft_id: str, *, origin: bool = True) -> Any:
    return client.post(f"/invoices/{draft_id}/flow/discard", headers=_ORIGIN if origin else {})


def _memory_snapshot() -> dict[str, Any]:
    """草稿以外的記憶體資料（案件清單與各式快取）；重新上傳不得改動它們。"""
    return {
        "cases": copy.deepcopy(invoices_router._INVOICE_STORE),
        "finance_cases": dict(invoices_router._FINANCE_CASES),
        "history": copy.deepcopy(dict(invoices_router._FINANCE_HISTORY)),
        "returns": copy.deepcopy(dict(invoices_router._RETURN_SNAPSHOTS)),
        "accounting": copy.deepcopy(invoices_router._ACCOUNTING_DRAFT),
    }


def test_duplicate_page_offers_a_discard_button_not_a_plain_link(
    browser_as: Any, api: FakeApi
) -> None:
    client, _ = browser_as(ME)
    draft_id = _start_draft(client)

    response = _duplicate_submit(client, api, draft_id)

    assert response.status_code == 200  # 沒有 403、沒有導向登入頁
    assert "此發票已存在於系統中" in response.text  # 既有的重複發票提示（API 拒絕後的橫幅）
    html = response.text
    assert f'formaction="/invoices/{draft_id}/flow/discard"' in html
    button = html[html.rindex("<button", 0, html.index("flow/discard")) :].split("</button>")[0]
    assert 'type="submit"' in button and "name=" not in button  # 不帶 action，不會被當成提交或檢核
    assert "data-reupload" in button
    assert "/flow/restart" not in html and 'href="/intake"' not in html
    assert "/cases/" not in html  # 沒有建立案件，也不連到既有案件


def test_check_reports_duplicate_and_page_still_offers_the_discard_button(
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
    assert f'formaction="/invoices/{draft_id}/flow/discard"' in paged.text


def test_discard_deletes_the_unsubmitted_draft_and_everything_in_it(
    browser_as: Any, api: FakeApi
) -> None:
    client, _ = browser_as(ME)
    draft_id = _start_draft(client)
    _duplicate_submit(client, api, draft_id)
    held = invoices_router._DRAFT_INVOICE_STORE[
        draft_id
    ]  # 若別處還握著這份草稿，內容也必須已被清空
    assert held.get("file_bytes") and held.get("intake_form") and held.get("recognition")

    response = _discard(client, draft_id)

    assert response.status_code == 303 and response.headers["location"] == "/invoices/new"
    assert draft_id not in invoices_router._DRAFT_INVOICE_STORE
    for key in ("file_bytes", "file_media_type", "recognition", "intake_form", "intake_state"):
        assert key not in held, key  # 辨識結果、核對資料、暫存影像都不再留在舊物件裡
    # 用舊草稿 id 取不回任何東西：本人（非財務）看到的是 403／404，而且不會有辨識結果或影像
    for path in (f"/invoices/{draft_id}/flow?step=3", f"/invoices/{draft_id}/file"):
        assert client.get(path).status_code in (403, 404), path
    leftover = invoices_router._DRAFT_INVOICE_STORE.get(draft_id) or {}
    assert not leftover.get("intake_form") and not leftover.get("file_bytes")


def test_discard_keeps_the_login_and_lands_on_a_new_upload_page(
    browser_as: Any, api: FakeApi
) -> None:
    client, module = browser_as(ME)
    first = _start_draft(client)
    _duplicate_submit(client, api, first)
    cookies_before = dict(client.cookies)

    response = _discard(client, first)

    assert "set-cookie" not in response.headers  # 沒有清除或改寫任何 Cookie
    new_flow = client.get(response.headers["location"])
    assert new_flow.status_code == 303
    second = new_flow.headers["location"].split("/")[2]
    assert new_flow.headers["location"] == f"/invoices/{second}/flow?step=1" and second != first
    page = client.get(new_flow.headers["location"])
    assert page.status_code == 200 and 'type="file"' in page.text  # 回到選檔步驟，仍登入
    assert dict(client.cookies) == cookies_before
    assert not [c for c in module.checks if c[0] == "inv.intake"]  # 沒有要求 inv.intake


def test_a_new_image_can_be_uploaded_after_discarding(browser_as: Any, api: FakeApi) -> None:
    client, _ = browser_as(ME)
    first = _start_draft(client)
    _duplicate_submit(client, api, first)
    _discard(client, first)
    api.routes["/api/v1/invoices"] = httpx.Response(201, json=SUBMITTED)
    before = len(api.requests)

    second = client.get("/invoices/new").headers["location"].split("/")[2]
    upload = _upload(client, second)

    assert (
        upload.status_code == 303
        and upload.headers["location"] == f"/invoices/{second}/flow?step=3"
    )
    assert api.paths()[before:] == ["/api/v1/invoices/recognize"]  # 只有新影像的辨識
    assert 'value="辦公文具"' in client.get(f"/invoices/{second}/flow?step=3").text
    assert first not in invoices_router._DRAFT_INVOICE_STORE  # 新影像沒有寫進舊草稿


def test_discard_never_calls_the_api_or_touches_cases(browser_as: Any, api: FakeApi) -> None:
    client, _ = browser_as(ME)
    draft_id = _start_draft(client)
    _duplicate_submit(client, api, draft_id)
    calls = len(api.requests)
    snapshot = _memory_snapshot()

    _discard(client, draft_id)

    assert len(api.requests) == calls  # 沒有任何 API 呼叫，更不會有刪除正式發票的呼叫
    assert all(r.method in ("POST", "GET") for r in api.requests)
    assert _memory_snapshot() == snapshot  # 案件清單、財務與退回快取都沒變


def test_discard_leaves_other_drafts_untouched_even_when_repeated(
    browser_as: Any, api: FakeApi
) -> None:
    mine, _ = browser_as(ME)
    keep = _start_draft(mine)
    _upload(mine, keep)
    target = _start_draft(mine)
    _duplicate_submit(mine, api, target)
    other, _ = browser_as(OTHER)
    others = _start_draft(other)
    invoices_router._DRAFT_INVOICE_STORE[others]["intake_form"] = "other-users-form"
    mine, _ = browser_as(ME)
    keep_snapshot = dict(invoices_router._DRAFT_INVOICE_STORE[keep])
    others_snapshot = dict(invoices_router._DRAFT_INVOICE_STORE[others])

    first = _discard(mine, target)
    second = _discard(mine, target)  # 重複點擊：草稿已不存在

    assert first.status_code == second.status_code == 303
    assert first.headers["location"] == second.headers["location"] == "/invoices/new"
    assert target not in invoices_router._DRAFT_INVOICE_STORE
    assert dict(invoices_router._DRAFT_INVOICE_STORE[keep]) == keep_snapshot
    assert dict(invoices_router._DRAFT_INVOICE_STORE[others]) == others_snapshot


def test_discard_of_a_missing_or_foreign_id_creates_and_deletes_nothing(
    browser_as: Any,
) -> None:
    client, _ = browser_as(ME)
    drafts_before = set(invoices_router._DRAFT_INVOICE_STORE)
    snapshot = _memory_snapshot()

    unknown = _discard(client, "cccccccc-0000-4000-8000-000000000003")  # 沒有這份草稿
    demo = _discard(client, "3")  # 發票清單裡的示範案件，不是草稿

    assert unknown.status_code == demo.status_code == 303
    assert unknown.headers["location"] == demo.headers["location"] == "/invoices/new"
    assert (
        set(invoices_router._DRAFT_INVOICE_STORE) == drafts_before
    )  # 沒有自動建立空草稿，也沒有刪除
    assert _memory_snapshot() == snapshot  # 示範案件還在


def test_discard_never_removes_a_submitted_draft(browser_as: Any, api: FakeApi) -> None:
    client, _ = browser_as(ME)
    draft_id = _start_draft(client)
    _upload(client, draft_id)
    client.post(f"/invoices/{draft_id}/flow/review", headers=_ORIGIN, data=review_data("submit"))
    calls = len(api.requests)

    response = _discard(client, draft_id)

    assert response.status_code == 303 and response.headers["location"] == "/invoices/new"
    kept = invoices_router._DRAFT_INVOICE_STORE.get(draft_id)
    assert kept is not None and kept.get("submitted_invoice_id")  # 已提交案件的結果頁資料保留
    assert len(api.requests) == calls  # 不呼叫 API：案件在資料庫，不動它


@pytest.mark.parametrize("intruder_department", ["採購部", "財務部"])
def test_another_users_draft_cannot_be_discarded(
    browser_as: Any, api: FakeApi, intruder_department: str
) -> None:
    owner, _ = browser_as(ME)
    draft_id = _start_draft(owner)
    _upload(owner, draft_id)
    intruder, _ = browser_as(OTHER, intruder_department)

    response = _discard(intruder, draft_id)

    assert response.status_code == 403
    draft = invoices_router._DRAFT_INVOICE_STORE.get(draft_id)
    assert draft is not None and draft.get("intake_form") is not None and draft.get("file_bytes")


@pytest.mark.parametrize("state", ["no_cookie", "expired"])
def test_discard_requires_a_valid_login(browser_as: Any, api: FakeApi, state: str) -> None:
    owner, _ = browser_as(ME)
    draft_id = _start_draft(owner)
    _upload(owner, draft_id)
    client, _ = browser_as(ME, cookie=state != "no_cookie", expired=state == "expired")

    response = _discard(client, draft_id)

    assert response.status_code in (303, 401)
    if response.status_code == 303:
        assert response.headers["location"].startswith("/login")
    draft = invoices_router._DRAFT_INVOICE_STORE.get(draft_id)
    assert draft is not None and draft.get("intake_form") is not None


def test_discard_requires_same_origin_and_post(browser_as: Any, api: FakeApi) -> None:
    client, _ = browser_as(ME)
    draft_id = _start_draft(client)
    _upload(client, draft_id)

    assert _discard(client, draft_id, origin=False).status_code == 403
    assert (
        client.get(f"/invoices/{draft_id}/flow/discard").status_code == 405
    )  # 有副作用，只接受 POST
    draft = invoices_router._DRAFT_INVOICE_STORE.get(draft_id)
    assert draft is not None and draft.get("intake_form") is not None


def test_a_failed_discard_is_reported_and_not_shown_as_success(
    browser_as: Any, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = browser_as(ME)
    draft_id = _start_draft(client)
    _upload(client, draft_id)

    class _Broken(type(invoices_router._DRAFT_INVOICE_STORE)):  # type: ignore[misc]
        def pop(self, key: Any, *args: Any) -> Any:
            raise RuntimeError("boom")

    broken = _Broken(invoices_router._DRAFT_INVOICE_STORE)
    monkeypatch.setattr(invoices_router, "_DRAFT_INVOICE_STORE", broken)
    response = _discard(client, draft_id)
    assert response.status_code == 500 and "location" not in response.headers
    assert broken[draft_id].get("intake_form") is not None  # 沒刪成就原樣保留，不假裝成功

    class _Stuck(type(invoices_router._DRAFT_INVOICE_STORE)):  # type: ignore[misc]
        def pop(self, key: Any, *args: Any) -> Any:
            return dict.get(self, key)  # 假裝刪了，其實還在

    stuck = _Stuck(broken)
    monkeypatch.setattr(invoices_router, "_DRAFT_INVOICE_STORE", stuck)
    response = _discard(client, draft_id)
    assert response.status_code == 500 and "location" not in response.headers


def test_reupload_flow_never_forbids_an_employee_without_intake_create(
    browser_as: Any, api: FakeApi
) -> None:
    client, module = browser_as(ME)
    draft_id = _start_draft(client)
    _duplicate_submit(client, api, draft_id)

    statuses = [
        _discard(client, draft_id).status_code,
        client.get("/invoices/new").status_code,
    ]

    assert statuses == [303, 303]  # 沒有任何 403
    assert client.get("/intake").status_code == 403  # /intake 本身的權限不變
    assert ("inv.upload", Action.VIEW) in module.checks


def test_new_drafts_never_collide_even_with_numeric_demo_ids(browser_as: Any) -> None:
    assert any(str(i["id"]).isdigit() for i in invoices_router._INVOICE_STORE)
    mine, _ = browser_as(ME)
    first = _start_draft(mine)
    other, _ = browser_as(OTHER)
    other_id = _start_draft(other)
    mine, _ = browser_as(ME)
    second = _start_draft(mine)

    assert len({first, other_id, second}) == 3
    assert invoices_router._DRAFT_INVOICE_STORE[other_id]["uploaded_by"] == OTHER


def test_restart_route_still_does_not_exist(browser_as: Any) -> None:
    client, _ = browser_as(ME)
    draft_id = _start_draft(client)

    assert client.post(f"/invoices/{draft_id}/flow/restart", headers=_ORIGIN).status_code in (
        404,
        405,
    )


def test_step3_has_no_duplicate_6_to_9_placeholder_but_keeps_item_table(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)

    html = admin_client.get(f"/invoices/{draft}/flow?step=3").text

    assert "請見下方" not in html and "<span>6～9</span>" not in html
    # 品名明細完整保留：欄位 name 不變，仍可新增／刪除、送出
    for name in ("item_item_name", "item_quantity", "item_unit_price", "item_line_amount"):
        assert f'name="{name}"' in html
    assert 'value="add_item"' in html and 'value="remove_item:0"' in html
    assert "品名明細（第 6～9 項" in html


def test_step3_layout_keeps_fields_1_to_4_and_shows_item_cards_with_total(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)

    html = admin_client.get(f"/invoices/{draft}/flow?step=3").text

    assert "<h3>發票基本資料</h3>" in html and "表頭 10 項" not in html
    # 1～4（聯式、字軌號碼、日期、買受人名稱）必須保留
    for expected in ("發票聯式／識別", "發票字軌號碼", "發票日期", "買受人名稱"):
        assert expected in html, expected
    for name in ("invoice_track", "invoice_number", "invoice_date", "buyer_name"):
        assert f'name="{name}"' in html, name
    # 11、12、13 各佔一列；5 與 10 同列（不加 --full）
    for num in ("11", "12", "13"):
        assert f"<span>{num}</span>" in html
    assert html.count("invoice14-field--full") == 4  # 11、12、13、14
    # 品名明細：一項次一張卡，上列品名、下列數量／單價／金額；欄位 name 不變
    assert html.count('class="item-card"') == 2 and "品名／用途" not in html
    for name in ("item_item_name", "item_quantity", "item_unit_price", "item_line_amount"):
        assert html.count(f'name="{name}"') == 2, name
    # 明細總金額：RECOGNITION 兩筆為 2,000 與 1,000
    assert "明細總金額" in html and "<strong data-items-total>3,000</strong>" in html
    assert "品名小計" not in html


# ---- 重新上傳：無論有沒有重複發票，都停在新的上傳頁（可選檔）、仍登入、沒有 403 或登入頁 ----


@pytest.mark.parametrize("department", ["採購部", "財務部"])
def test_reupload_without_a_duplicate_also_lands_on_the_new_upload_page(
    browser_as: Any, api: FakeApi, department: str
) -> None:
    client, _ = browser_as(ME, department)
    draft_id = _start_draft(client)
    _upload(client, draft_id)  # 一般的核對頁：沒有重複發票
    review = client.get(f"/invoices/{draft_id}/flow?step=3")
    assert f'formaction="/invoices/{draft_id}/flow/discard"' in review.text

    discard = _discard(client, draft_id)
    new = client.get(discard.headers["location"])
    page = client.get(new.headers["location"])

    assert [discard.status_code, new.status_code, page.status_code] == [303, 303, 200]
    assert 'type="file"' in page.text  # 可以選檔
    assert draft_id not in invoices_router._DRAFT_INVOICE_STORE
    assert all(r.method != "DELETE" for r in api.requests)


@pytest.mark.parametrize("department", ["採購部", "財務部"])
def test_new_upload_page_works_when_the_next_request_hits_an_instance_without_the_draft(
    browser_as: Any, api: FakeApi, department: str
) -> None:
    """Vercel 可能把下一個請求交給沒有這份記憶體草稿的實例：一般員工不能因此得到 403。"""
    client, _ = browser_as(ME, department)
    new = client.get("/invoices/new")
    draft_id = new.headers["location"].split("/")[2]
    invoices_router._DRAFT_INVOICE_STORE.pop(draft_id)  # 模擬另一個實例：沒有這份草稿

    page = client.get(new.headers["location"])
    assert page.status_code == 200 and 'type="file"' in page.text

    invoices_router._DRAFT_INVOICE_STORE.pop(draft_id, None)  # 上傳時又換了一個實例
    upload = _upload(client, draft_id)
    assert (
        upload.status_code == 303
        and upload.headers["location"] == f"/invoices/{draft_id}/flow?step=3"
    )
    assert invoices_router._DRAFT_INVOICE_STORE[draft_id]["uploaded_by"] == ME


def test_recreated_empty_drafts_do_not_open_up_other_pages_or_other_peoples_drafts(
    browser_as: Any, api: FakeApi
) -> None:
    owner, _ = browser_as(ME)
    draft_id = _start_draft(owner)
    _upload(owner, draft_id)
    intruder, _ = browser_as(OTHER)

    # 這個實例上已有別人的草稿：照舊 403，不會被接手
    assert intruder.get(f"/invoices/{draft_id}/flow?step=1").status_code == 403
    assert _upload(intruder, draft_id).status_code == 403
    assert invoices_router._DRAFT_INVOICE_STORE[draft_id]["uploaded_by"] == ME
    # 只有步驟 1 會替本人重建空草稿；其他步驟對沒看過的 id 仍是 403
    unknown = "dddddddd-0000-4000-8000-000000000004"
    assert intruder.get(f"/invoices/{unknown}/flow?step=3").status_code == 403


# ---- 方案 C：步驟 1 的 GET 只顯示選檔頁（不寫入草稿字典）；真正上傳時才建立並保存本人草稿 ----
# 下面用兩個各自獨立的記憶體字典模擬兩個伺服器實例；這是離線模擬，不是真實 Vercel 多實例驗證。
# 已知限制（本次不解決）：上傳與核對被不同實例處理時，核對會回 404；發票清單頁會清掉該實例所有人的草稿。


class _Fleet:
    """兩個模擬實例：切換時只換掉模組內的草稿字典，其他程式與資料完全相同。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mp = monkeypatch
        self.stores = {"A": invoices_router._IdKeyedDict(), "B": invoices_router._IdKeyedDict()}

    def on(self, name: str) -> Any:
        self._mp.setattr(invoices_router, "_DRAFT_INVOICE_STORE", self.stores[name])
        return self.stores[name]


@pytest.fixture
def fleet(monkeypatch: pytest.MonkeyPatch) -> _Fleet:
    return _Fleet(monkeypatch)


UNKNOWN_ID = "dddddddd-0000-4000-8000-000000000004"


@pytest.mark.parametrize("department", ["採購部", "財務部"])
def test_step1_get_shows_the_upload_page_without_saving_any_draft(
    browser_as: Any, department: str
) -> None:
    client, _ = browser_as(ME, department)
    drafts_before = dict(invoices_router._DRAFT_INVOICE_STORE)
    snapshot = _memory_snapshot()

    page = client.get(f"/invoices/{UNKNOWN_ID}/flow?step=1")

    assert page.status_code == 200 and 'type="file"' in page.text  # 可以立即選檔
    assert UNKNOWN_ID not in invoices_router._DRAFT_INVOICE_STORE  # 單純開頁不補寫草稿
    assert dict(invoices_router._DRAFT_INVOICE_STORE) == drafts_before  # 其他草稿也沒被動
    assert _memory_snapshot() == snapshot
    assert 'action="/invoices/' in page.text and f"/invoices/{UNKNOWN_ID}/flow/upload" in page.text


def test_step1_get_never_overwrites_or_takes_over_an_existing_draft(
    browser_as: Any, api: FakeApi
) -> None:
    owner, _ = browser_as(ME)
    draft_id = _start_draft(owner)
    _upload(owner, draft_id)
    held = invoices_router._DRAFT_INVOICE_STORE[draft_id]
    before = dict(held)

    assert owner.get(f"/invoices/{draft_id}/flow?step=1").status_code == 200
    assert (
        invoices_router._DRAFT_INVOICE_STORE[draft_id] is held and dict(held) == before
    )  # 本人：不覆寫

    intruder, _ = browser_as(OTHER)
    assert intruder.get(f"/invoices/{draft_id}/flow?step=1").status_code == 403  # 他人：不可接手
    assert invoices_router._DRAFT_INVOICE_STORE[draft_id] is held and dict(held) == before


@pytest.mark.parametrize("department", ["採購部", "財務部"])
def test_upload_without_a_draft_creates_and_saves_the_users_own_draft(
    browser_as: Any, api: FakeApi, department: str
) -> None:
    client, _ = browser_as(ME, department)
    assert UNKNOWN_ID not in invoices_router._DRAFT_INVOICE_STORE

    upload = _upload(client, UNKNOWN_ID)

    assert upload.status_code == 303
    assert upload.headers["location"] == f"/invoices/{UNKNOWN_ID}/flow?step=3"
    draft = invoices_router._DRAFT_INVOICE_STORE[UNKNOWN_ID]  # 真正上傳時才建立並保存
    assert draft["uploaded_by"] == ME and draft["id"] == UNKNOWN_ID  # 上傳者是目前登入者、id 正確
    assert draft.get("intake_form") is not None and draft.get("file_bytes")
    assert api.paths() == ["/api/v1/invoices/recognize"]  # 只有辨識，沒有提交


def test_upload_never_takes_over_another_users_draft(browser_as: Any, api: FakeApi) -> None:
    owner, _ = browser_as(ME)
    draft_id = _start_draft(owner)
    _upload(owner, draft_id)
    held = invoices_router._DRAFT_INVOICE_STORE[draft_id]
    calls = len(api.requests)
    intruder, _ = browser_as(OTHER)

    assert _upload(intruder, draft_id).status_code == 403
    assert invoices_router._DRAFT_INVOICE_STORE[draft_id] is held and held["uploaded_by"] == ME
    assert len(api.requests) == calls  # 被拒絕時沒有呼叫 OCR


def test_other_steps_never_hand_a_regular_employee_an_empty_draft(browser_as: Any) -> None:
    client, _ = browser_as(ME)

    for step in (3, 4):
        assert client.get(f"/invoices/{UNKNOWN_ID}/flow?step={step}").status_code == 403
    assert client.post(
        f"/invoices/{UNKNOWN_ID}/flow/review", headers=_ORIGIN, data=review_data("check")
    ).status_code in (403, 404)
    owned = [d for d in invoices_router._DRAFT_INVOICE_STORE.values() if d.get("uploaded_by") == ME]
    assert owned == []  # 沒有任何屬於本人的可操作空草稿被補建


def test_a_real_case_is_never_turned_into_a_draft_by_step1(
    browser_as: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id = "eeeeeeee-0000-4000-8000-000000000005"
    case = {"id": case_id, "uploaded_by": OTHER, "stage": 9, "status": "approved"}
    monkeypatch.setattr(
        invoices_router.invoice_review_service,
        "fetch_invoice",
        lambda i: case if i == case_id else None,
    )
    client, _ = browser_as(ME)  # 非財務、非上傳者
    snapshot = _memory_snapshot()

    response = client.get(f"/invoices/{case_id}/flow?step=1")

    assert response.status_code == 403  # 走原本的案件權限，不是新草稿
    assert case_id not in invoices_router._DRAFT_INVOICE_STORE
    assert _memory_snapshot() == snapshot
    demo = client.get("/invoices/3/flow?step=1")  # 發票清單裡的示範案件也一樣：不轉成草稿
    assert demo.status_code in (200, 403) and "3" not in invoices_router._DRAFT_INVOICE_STORE


def test_cross_instance_step1_is_served_without_leaving_an_empty_draft(
    browser_as: Any, fleet: _Fleet
) -> None:
    client, _ = browser_as(ME)  # 一般員工
    fleet.on("A")
    new = client.get("/invoices/new")
    draft_id = new.headers["location"].split("/")[2]
    assert draft_id in fleet.stores["A"]

    fleet.on("B")  # 下一個請求被交給沒有這份草稿的實例
    page = client.get(new.headers["location"])

    assert page.status_code == 200 and 'type="file"' in page.text
    assert draft_id not in fleet.stores["B"] and len(fleet.stores["B"]) == 0  # 沒有留下無用的空草稿


def test_cross_instance_upload_creates_the_users_draft_only_on_the_serving_instance(
    browser_as: Any, api: FakeApi, fleet: _Fleet
) -> None:
    client, _ = browser_as(ME)
    fleet.on("A")
    draft_id = client.get("/invoices/new").headers["location"].split("/")[2]

    fleet.on("B")
    upload = _upload(client, draft_id)

    assert upload.status_code == 303
    assert fleet.stores["B"][draft_id]["uploaded_by"] == ME
    assert fleet.stores["B"][draft_id].get("intake_form") is not None
    assert (
        fleet.stores["A"][draft_id].get("intake_form") is None
    )  # 實例 A 的空草稿沒有被「補上」資料


def test_cross_instance_review_cannot_pretend_the_recognition_exists_or_submit(
    browser_as: Any, api: FakeApi, fleet: _Fleet
) -> None:
    """已知限制：上傳在 B、核對回到 A 時會失敗；失敗必須誠實，不能顯示成功、不能提交、不能讓別人讀到 B 的資料。"""
    client, _ = browser_as(ME)
    fleet.on("A")
    draft_id = client.get("/invoices/new").headers["location"].split("/")[2]
    fleet.on("B")
    _upload(client, draft_id)
    fleet.on("A")
    calls = len(api.requests)

    page = client.get(f"/invoices/{draft_id}/flow?step=3")
    review = client.post(
        f"/invoices/{draft_id}/flow/review",
        headers=_ORIGIN,
        data=review_data("submit", recognition_id=RID),
    )
    live = client.post(
        f"/invoices/{draft_id}/flow/check", headers=_ORIGIN, data=review_data("check")
    )

    assert review.status_code == 404 and live.status_code == 404  # 找不到辨識中的草稿，而不是成功
    assert len(api.requests) == calls  # 沒有任何提交或檢核請求送到 API
    assert "/cases/" not in review.text and "submitted" not in review.text.lower()
    assert (
        "flow-review-form" not in page.text and "辦公文具" not in page.text
    )  # 不顯示 B 的辨識結果
    fleet.on("B")  # 別人在擁有這份草稿的實例上也讀不到
    intruder, _ = browser_as(OTHER)
    assert intruder.get(f"/invoices/{draft_id}/flow?step=3").status_code == 403
    assert fleet.stores["B"][draft_id]["uploaded_by"] == ME
