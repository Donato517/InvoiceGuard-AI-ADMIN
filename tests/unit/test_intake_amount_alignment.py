"""測試目標：ADMIN 核對表單的金額功能對齊手機前台——國字大寫異常警示、金額連動腳本的載入與欄位接點、
修改國字後重新檢核、提交資料保留人工修正值。

替身測試（假 EModule、假 API 傳輸）：不連 DB、Storage、AI 或真實 API。
注意：國字警示由 API 的 check-warnings 產生（API 本機修正尚未合併／部署）；這裡只驗證 ADMIN 把 API 回的警示
顯示在國字欄位旁、不過濾代碼、不複製解析規則。API 端是否真的產生警示，不在 ADMIN 測試範圍。
"""

# ruff: noqa: F811  (pytest fixture 以名稱注入，匯入的 fixture 會被測試參數同名遮蔽)

import re
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from starlette.datastructures import FormData
from test_intake_routes import ORIGIN, FakeApi, api, module, review_data, web  # noqa: F401

from app.services import invoice_intake_mapping as mapping

ANOMALY = "億仟佰拾壹萬肆仟捌佰伍拾捌元"
CHECK_WARNINGS = "/api/v1/invoices/check-warnings"
ZH_UNREADABLE = {
    "field": "total_amount_zh",
    "code": "total_amount_zh_unreadable",
    "message": "國字大寫金額格式無法辨識，請對照票面人工確認",
}
ZH_MISMATCH = {
    "field": "total_amount_zh",
    "code": "total_amount_zh_mismatch",
    "message": "國字大寫金額與總計不符，請確認",
}


def _warnings(api: FakeApi, *items: dict[str, str]) -> None:
    api.routes[CHECK_WARNINGS] = httpx.Response(
        200, json={"warnings": list(items), "checked_on": "2026-11-05"}
    )


def _zh_notes(html: str) -> str:
    """國字大寫欄位旁的提醒容器內容（data-check-field="total_amount_zh"）。"""
    match = re.search(r'data-check-field="total_amount_zh">(.*?)</div>', html, re.S)
    assert match, "找不到國字大寫欄位的提醒容器"
    return match.group(1)


def test_review_form_loads_amount_script_and_exposes_the_field_hooks(
    web: TestClient,
    api: FakeApi,
) -> None:
    text = web.post("/intake/review", headers=ORIGIN, data=review_data("check")).text
    assert '<script src="/static/js/intake-amounts.js' in text
    for hook in ("f-sales_amount", "f-tax_amount", "f-total_amount", "f-total_amount_zh"):
        assert f'id="{hook}"' in text, hook
    for key in ("total_amount", "total_amount_zh"):
        assert f'data-check-field="{key}"' in text, key


def test_api_zh_unreadable_warning_is_shown_next_to_the_zh_field(
    web: TestClient,
    api: FakeApi,
) -> None:
    _warnings(api, ZH_UNREADABLE)
    text = web.post(
        "/intake/review",
        headers=ORIGIN,
        data=review_data("check", total_amount="14858", total_amount_zh=ANOMALY),
    ).text
    assert ZH_UNREADABLE["message"] in _zh_notes(text)
    assert 'data-check-kind="warning" data-severity="warning"' in _zh_notes(text)
    assert f'value="{ANOMALY}"' in text  # 使用者／AI 的原值照樣回填，不被刪除或改寫
    sent = api.last_json(CHECK_WARNINGS)
    assert sent["total_amount_zh"] == ANOMALY and sent["total_amount"] == "14858"


def test_api_zh_mismatch_warning_is_shown_and_total_warning_stays_separate(
    web: TestClient,
    api: FakeApi,
) -> None:
    total = {
        "field": "total_amount",
        "code": "total_amount_mismatch",
        "message": "銷售額＋稅額與總計不符，請確認",
    }
    _warnings(api, total, ZH_MISMATCH)
    text = web.post(
        "/intake/review",
        headers=ORIGIN,
        data=review_data("check", total_amount="14858", total_amount_zh="壹萬肆仟捌佰伍拾玖元整"),
    ).text
    assert ZH_MISMATCH["message"] in _zh_notes(text)
    assert total["message"] not in _zh_notes(text)
    assert total["message"] in text


def test_normal_amount_and_zh_show_no_zh_warning(web: TestClient, api: FakeApi) -> None:
    _warnings(api)  # API 判定一致：沒有警示
    text = web.post(
        "/intake/review",
        headers=ORIGIN,
        data=review_data(
            "check",
            sales_amount="14150",
            tax_amount="708",
            total_amount="14858",
            total_amount_zh="壹萬肆仟捌佰伍拾捌元整",
        ),
    ).text
    assert "field-warning" not in _zh_notes(text)


def test_recheck_after_manual_zh_edit_uses_the_edited_value(web: TestClient, api: FakeApi) -> None:
    _warnings(api, ZH_UNREADABLE)
    first = web.post(
        "/intake/review",
        headers=ORIGIN,
        data=review_data("check", total_amount="14858", total_amount_zh=ANOMALY),
    ).text
    assert ZH_UNREADABLE["message"] in _zh_notes(first)
    _warnings(api)  # 人工改成正確國字後，API 不再回警示
    second = web.post(
        "/intake/review",
        headers=ORIGIN,
        data=review_data("check", total_amount="14858", total_amount_zh="壹萬肆仟捌佰伍拾捌元整"),
    ).text
    assert "field-warning" not in _zh_notes(second)
    assert api.last_json(CHECK_WARNINGS)["total_amount_zh"] == "壹萬肆仟捌佰伍拾捌元整"


def test_mapping_passes_amounts_and_zh_unchanged_to_check_and_submit() -> None:
    data = review_data("submit", total_amount="1,4858", total_amount_zh=f" {ANOMALY} ")
    pairs = [
        (key, item)
        for key, value in data.items()
        for item in (value if isinstance(value, list) else [value])
    ]
    form = mapping.form_from_post(FormData(pairs))
    body = mapping.submit_body(form)
    assert body["total_amount"] == "14858"  # 千分位逗號由既有整理移除
    assert body["total_amount_zh"] == ANOMALY  # 只去前後空白；不解析、不刪前綴
    assert (
        body["changes"] == []
    )  # 修改紀錄由 API 依 recognition_id 與 AI 原值比對，ADMIN 不自行改寫


# ---- 與新版即時檢核（invoice-flow-review.js）共存的靜態守門：兩支腳本的標記不能互相清除 ----


def _js(name: str) -> str:
    return (Path(__file__).resolve().parents[2] / "app" / "static" / "js" / name).read_text(
        encoding="utf-8"
    )


def test_amounts_script_never_marks_its_notes_with_data_live() -> None:
    flow = _js("invoice-flow-review.js")
    amounts = _js("intake-amounts.js")
    # 新版伺服器即時檢核每一輪會移除表單內所有 [data-live]；本機金額提醒若也用它就會被清掉
    assert 'querySelectorAll("[data-live]")' in flow
    assert not re.search(r"""setAttribute\(\s*['"]data-live['"]""", amounts)
    assert "data-amounts-note" in amounts
