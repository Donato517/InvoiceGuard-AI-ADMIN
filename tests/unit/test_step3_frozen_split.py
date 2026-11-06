# ruff: noqa: F811
"""測試目標：步驟 3 人工核對的凍結分欄（correction-split--frozen）只套用在步驟 3。

涵蓋：兩條步驟 3 Template 分支都有專用 class、步驟 5 沒有、表單欄位與 action 不變、
POST flow/discard 不變、凍結 CSS 只在「寬 ≥ 1101px 且高度足夠」的 media query 內（手機版不被覆蓋）、
步驟 5 的 .fr-split 規則不變。版面實際行為（捲動、高度）由隔離瀏覽器驗收，不在這裡斷言。
合成資料：沿用 test_invoice_flow_intake 的假 API 替身，不連 API、DB 或 Storage。
"""

import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_intake_routes import FakeApi, api, review_data  # noqa: F401
from test_invoice_flow_intake import (  # noqa: F401
    _isolated_draft_store,
    _upload,
    draft,
    flow_api,
)
from test_procurement_pages import _restore_demo_state  # noqa: F401
from test_procurement_pages import client as returned_client  # noqa: F401

CSS = Path(__file__).resolve().parents[2] / "app/static/css/pages/invoice-flow.css"
_ORIGIN = {"Origin": "http://testserver"}
FROZEN = "invoice-correction-split correction-split--frozen"


def _frozen_media_block(css: str) -> tuple[int, int]:
    """回傳凍結規則所在的 media query 區塊（寬 ≥ 1101px）的起訖位置，以大括號配對。"""
    marker = css.index(".correction-split--frozen {")
    start = css.rindex("@media (min-width: 1101px)", 0, marker)
    depth = 0
    for index in range(css.index("{", start), len(css)):
        depth += {"{": 1, "}": -1}.get(css[index], 0)
        if depth == 0:
            return start, index
    raise AssertionError("凍結 media query 沒有結尾")


def test_api_step3_uses_the_frozen_class_and_keeps_form_and_discard(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)

    html = admin_client.get(f"/invoices/{draft}/flow?step=3").text

    assert FROZEN in html
    assert "correction-split--notice" not in html  # 沒有錯誤訊息時不加提示修飾 class
    assert "fr-split" not in html
    assert f'action="/invoices/{draft}/flow/review"' in html
    assert 'id="flow-review-form"' in html
    # 重新上傳仍是 POST flow/discard 的按鈕，不是 GET /invoices/new 連結
    assert f'formaction="/invoices/{draft}/flow/discard"' in html
    assert 'href="/invoices/new" data-reupload' not in html


def test_api_step3_with_banner_adds_the_notice_modifier(
    admin_client: TestClient, flow_api: FakeApi, draft: str
) -> None:
    _upload(admin_client, draft)
    flow_api.routes["/api/v1/invoices/check-required"] = httpx.Response(500, json={})

    page = admin_client.post(
        f"/invoices/{draft}/flow/review",
        headers=_ORIGIN,
        data=review_data("check"),
    )

    assert page.status_code == 200
    assert 'class="alert alert--danger"' in page.text
    assert f"{FROZEN} correction-split--notice" in page.text


def test_legacy_step3_branch_uses_the_frozen_class_and_keeps_its_form(
    admin_client: TestClient,
) -> None:
    html = admin_client.get("/invoices/3/flow?step=3").text

    assert f"{FROZEN} correction-split--notice" in html
    assert 'id="flow-correct-form"' in html
    assert 'action="/invoices/3/flow/correct"' in html
    assert "fr-split" not in html


@pytest.mark.parametrize("invoice_id", ["1", "2", "3"])
def test_step5_never_gets_the_step3_frozen_class(admin_client: TestClient, invoice_id: str) -> None:
    response = admin_client.get(f"/invoices/{invoice_id}/flow?step=5")

    assert response.status_code == 200
    assert "correction-split--frozen" not in response.text
    if "invoice-correction-split" in response.text:
        assert "invoice-correction-split fr-split" in response.text


def test_returned_case_step3_is_not_frozen(returned_client: TestClient) -> None:
    """退回補正區塊在分欄上方會把分欄推到畫面外，這個情境退回一般捲動。"""
    html = returned_client.get("/invoices/3/flow?step=3").text

    assert "退回補正" in html
    assert "invoice-correction-split" in html
    assert "correction-split--frozen" not in html
    assert 'id="flow-correct-form"' in html


def test_frozen_css_applies_on_wide_viewports_with_a_step5_style_height_floor() -> None:
    css = CSS.read_text(encoding="utf-8")
    start, end = _frozen_media_block(css)

    uses = [match.start() for match in re.finditer(r"correction-split--frozen", css)]
    assert uses, "找不到凍結規則"
    assert all(start < position < end for position in uses)  # 窄螢幕（≤1100px）完全不受影響
    block = css[start:end]
    # 比照步驟 5：只看寬度，沒有高度門檻；矮視窗停在 32rem 下限（外層頁面多捲一段）
    assert "(min-height:" not in css[start : css.index("{", start)]
    assert "max(32rem, calc(100vh - var(--frozen-offset)))" in block
    assert "max(32rem, calc(100dvh - var(--frozen-offset)))" in block  # dvh 有 vh 後備
    assert "overflow-y: auto" in block
    assert "min-height: 0" in block
    assert "overflow: hidden" not in block  # 不用 hidden 掩蓋高度問題
    assert "--frozen-offset: 31rem" in block  # 分欄上方有提示時多扣的高度
    assert "--frozen-offset: 15rem" in block  # 退回補正頁（--compact）版面較少
    # 單欄規則仍在 1100px 以下，與凍結的 1101px 互補
    assert "@media (max-width: 1100px)" in css


def test_step5_fr_split_rules_are_unchanged() -> None:
    css = CSS.read_text(encoding="utf-8")
    block = css[css.index("@media (min-width: 1101px) {\n  .fr-split {") :]
    block = block[: block.index("\n}\n") + 3]

    assert "height: calc(100vh - 12rem);" in block
    assert "min-height: 32rem;" in block
    assert "correction-split--frozen" not in block
