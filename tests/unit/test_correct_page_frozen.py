# ruff: noqa: F811
"""測試目標：退回補正頁（/invoices/{id}/correct）的凍結分欄與版面。

涵蓋：可補正時分欄加上 correction-split--frozen、第 N 輪提示與財務退回原因放在右欄最上方
（分欄上方的版面高度固定，凍結高度才算得準）、終止／不可補正時沒有分欄且原因仍在卡片最上方、
表單欄位與 id 不變、步驟 5 不受影響。版面的實際捲動行為由隔離瀏覽器驗收，不在這裡斷言。
合成資料：沿用 test_invoice_correction 的假 API 替身，不連 API、DB 或 Storage。
"""

import httpx
from fastapi.testclient import TestClient
from test_invoice_correction import (  # noqa: F401
    INVOICE_ID,
    OTHER_ID,
    Env,
    FakeApi,
    _page,
    api,
    browser,
    env,
    return_items_json,
)


def _split_html(html: str) -> str:
    """分欄（invoice-correction-split）內的 HTML。"""
    start = html.index('<div class="invoice-correction-split')
    return html[start:]


def test_correct_page_split_is_frozen_and_keeps_its_form(browser: TestClient, api: FakeApi) -> None:
    html = _page(browser).text

    assert (
        '<div class="invoice-correction-split correction-split--frozen correction-split--compact">'
        in html
    )
    assert "fr-split" not in html
    assert 'id="correct-form"' in html and 'id="correct-submit"' in html
    assert 'data-field="total_amount"' in html
    assert "終止並重新上傳" not in html  # 可補正時不出現終止按鈕


def test_return_reasons_move_into_the_right_column_when_the_case_is_correctable(
    browser: TestClient, api: FakeApi
) -> None:
    html = _page(browser).text

    assert html.count('id="return-reasons"') == 1  # 只出現一次，沒有重複
    assert html.count("<h2>財務退回原因</h2>") == 1
    assert html.count("提交被退回。只有財務指定的欄位可以修改") == 1
    split = _split_html(html)
    field_column = split[split.index('<section class="correction-field-column"') :]
    # 原因與提示在右欄內，且在表單之前
    assert field_column.index("財務退回原因") < field_column.index('id="correct-form"')
    assert field_column.index('id="return-reasons"') < field_column.index('id="correct-form"')
    assert "總計與明細不符" in field_column
    # 分欄之前（卡片最上方）不再有原因區塊，凍結窗格上方的版面高度才固定
    assert "財務退回原因" not in html[: html.index('<div class="invoice-correction-split')]


def test_image_return_has_no_split_and_keeps_reasons_at_the_top(
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

    assert "invoice-correction-split" not in html
    assert "correction-split--frozen" not in html
    assert html.count('id="return-reasons"') == 1 and "影像模糊" in html
    assert html.index("財務退回原因") < html.index("終止並重新上傳")


def test_blocked_case_has_no_split_and_keeps_reasons_at_the_top(
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

    assert "correction-split--frozen" not in html
    assert html.count('id="return-reasons"') == 1 and "請補紙本" in html
    assert html.index("財務退回原因") < html.index("沒有可補正的欄位")


def test_missing_return_items_notice_is_still_shown_inside_the_split(
    browser: TestClient, api: FakeApi
) -> None:
    api.responses["return-items"] = httpx.Response(200, json=return_items_json(return_items=[]))

    html = _page(browser).text

    assert html.count("data-no-return-items") == 1
    assert "correction-split--frozen" in html
    assert INVOICE_ID in html  # 頁面仍屬於該案件
