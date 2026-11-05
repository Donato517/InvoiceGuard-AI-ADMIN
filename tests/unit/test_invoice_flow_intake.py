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
