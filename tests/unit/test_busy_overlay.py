"""
模組名稱：tests.unit.test_busy_overlay
功能說明：送出請求後的「處理中」全頁遮罩涵蓋範圍——一般 POST／GET 表單、HTMX 寫入請求、以 fetch 送出的補正頁。
相依模組：app/static/js/main.js、app/static/js/invoice-correct.js、app/templates
注意事項：
    - 這裡是靜態檢查（腳本內容與樣板的明確例外清單），遮罩的實際畫面行為已用瀏覽器以靜態輸出頁驗證；
      不是真實 API、資料庫或正式站的驗收。
    - data-busy="" 是刻意不顯示遮罩的例外（登入有按鈕載入狀態、下載檔案不換頁、即時查詢），新增例外要同步更新清單。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN_JS = (ROOT / "app/static/js/main.js").read_text(encoding="utf-8")
BUSY_JS = (ROOT / "app/static/js/busy.js").read_text(encoding="utf-8")
CORRECT_JS = (ROOT / "app/static/js/invoice-correct.js").read_text(encoding="utf-8")
BASE_HTML = (ROOT / "app/templates/base.html").read_text(encoding="utf-8")

# 刻意關閉遮罩的樣板（路徑與理由）；其他表單都應顯示遮罩
OPT_OUT_TEMPLATES = {
    "pages/auth/login.html": "登入按鈕自己有載入狀態",
    "pages/invoices/_batch_filing_card.html": "成功時是檔案下載、不換頁",
    "pages/knowledge/chat.html": "切換部門只更新清單（即時查詢）",
    "pages/knowledge/eval.html": "題庫逐題執行有自己的進度顯示",
    "pages/knowledge/_eval_result.html": "題庫逐題執行有自己的進度顯示",
}


def test_busy_js_loads_before_main_js_on_every_page() -> None:
    """base.html 是唯一的完整文件，所有頁面都靠它載入；busy.js 必須排在 main.js 之前。"""
    html_files = [
        p for p in (ROOT / "app/templates").rglob("*.html") if "<html" in p.read_text("utf-8")
    ]
    assert [p.name for p in html_files] == ["base.html"]
    assert BASE_HTML.index("js/busy.js") < BASE_HTML.index("js/main.js")


def test_post_forms_show_the_overlay_by_default_but_get_forms_do_not() -> None:
    assert "const DEFAULT_BUSY_MESSAGE = '處理中，請稍候…'" in MAIN_JS
    # GET（查詢、篩選）預設不顯示全頁遮罩（模式 C）；只有 POST 預設顯示，GET 要用非空 data-busy 才顯示
    assert "DEFAULT_LOAD_MESSAGE" not in MAIN_JS and "載入中，請稍候" not in MAIN_JS
    assert "} else if (isPost && !isHtmx && !opensElsewhere) {" in MAIN_JS
    # 明確的 data-busy（含空字串）優先；已被取消（確認視窗按取消、其他腳本自行處理）的送出不顯示
    assert "if (form.dataset.busy !== undefined) {" in MAIN_JS
    assert "event.defaultPrevented" in MAIN_JS
    assert "opensElsewhere" in MAIN_JS


def test_htmx_overlay_is_owned_per_request_and_released_only_by_itself() -> None:
    # 舊設計（任何 afterRequest 都關掉遮罩、以 setBusy 單一狀態管理）已移除
    assert "setBusy" not in MAIN_JS
    assert "htmx:afterRequest', () =>" not in MAIN_JS
    # 請求確定要送出（beforeSend）才取得 Token；以 detail.xhr 識別同一個請求
    block = MAIN_JS[MAIN_JS.index("htmx:beforeSend") :]
    assert "Busy.acquire(message)" in block and "htmxBusyReleases.set(xhr" in block
    assert "marked ? marked.dataset.busy" in block  # 明確的 data-busy（含空字串）優先
    assert "verb !== 'get' ? DEFAULT_BUSY_MESSAGE" in block
    # 結束／失敗／中止／逾時事件都只釋放自己的 Token
    assert (
        "for (const name of ['afterRequest', 'sendError', 'sendAbort', 'timeout', 'responseError'])"
        in MAIN_JS
    )
    assert "htmxBusyReleases.get(xhr)" in MAIN_JS and "htmxBusyReleases.delete(xhr)" in MAIN_JS


def test_navigation_overlay_has_no_fixed_timeout_and_is_cleared_by_pageshow() -> None:
    assert "setTimeout" not in BUSY_JS and "setInterval" not in BUSY_JS
    assert MAIN_JS.count("Busy.clearAll()") == 2  # 兩個 pageshow 處理
    assert "event.persisted" in MAIN_JS


def test_busy_manager_exposes_owner_tokens_and_a_release_function() -> None:
    assert "const token = Symbol('busy')" in BUSY_JS
    assert "owners.delete(token)" in BUSY_JS
    assert "root.invoiceGuardBusy = acquire" in BUSY_JS


def test_overlay_follows_dialog_open_and_close_instead_of_blocking_escape() -> None:
    """Escape 第二次按下時 cancel 事件不可取消（瀏覽器仍會關閉對話框），所以不擋關閉，改讓遮罩跟著重新安置。"""
    assert "new MutationObserver" in BUSY_JS
    assert "attributeFilter: ['open']" in BUSY_JS and "subtree: true" in BUSY_JS
    assert "record.target.tagName === 'DIALOG'" in BUSY_JS and "owners.size > 0" in BUSY_JS
    assert "preventDefault" not in BUSY_JS and "'cancel'" not in BUSY_JS


def test_correction_page_fetch_uses_its_own_token_and_always_releases_it() -> None:
    assert "window.invoiceGuardBusy" in CORRECT_JS
    send = CORRECT_JS[CORRECT_JS.index("async function send()") :]
    send = send[: send.index("async function sendRequest()")]
    assert "const releaseBusy = startBusy('正在送出補正，請稍候…')" in send
    assert re.search(r"finally\s*\{\s*inFlight = false;\s*releaseBusy\(\);", send)
    void = CORRECT_JS[CORRECT_JS.index("voidButton.addEventListener") :]
    void = void[: void.index("const form = document.getElementById('correct-form')")]
    assert "const releaseBusy = startBusy('正在終止案件，請稍候…')" in void
    assert re.search(r"finally\s*\{\s*releaseBusy\(\);\s*voiding = false;", void)
    assert "busy(" not in CORRECT_JS.replace("startBusy(", "").replace("releaseBusy(", "")


def test_correction_page_blocks_duplicate_submit_and_recovers_after_unexpected_errors() -> None:
    send = CORRECT_JS[CORRECT_JS.index("async function send()") :]
    send = send[: send.index("async function sendRequest()")]
    assert "if (inFlight || form.dataset.done === 'true') return;" in send
    assert "catch (error)" in send and "submitButton.disabled = false;" in send
    assert "requestStarted" in send  # 是否已送出，決定例外後的說法
    assert "form.dataset.done !== 'true'" in send  # 已成功的提交不重新開放
    assert "if (voiding) return;" in CORRECT_JS


def test_only_listed_templates_opt_out_of_the_overlay() -> None:
    found = {
        str(path.relative_to(ROOT / "app/templates")).replace("\\", "/")
        for path in (ROOT / "app/templates").rglob("*.html")
        if 'data-busy=""' in path.read_text(encoding="utf-8")
    }
    assert found == set(OPT_OUT_TEMPLATES), (
        '新增或移除 data-busy="" 例外時，請更新 OPT_OUT_TEMPLATES 並說明理由：'
        f"{sorted(found ^ set(OPT_OUT_TEMPLATES))}"
    )


def test_no_form_opens_a_new_tab_without_being_exempt() -> None:
    """target=_blank 的表單不換頁，遮罩邏輯本來就略過；若有人移除這個略過，這裡會抓到。"""
    assert "opensElsewhere" in MAIN_JS
    uncovered = []
    for path in (ROOT / "app/templates").rglob("*.html"):
        for match in re.finditer(r"<form\b(.*?)>", path.read_text(encoding="utf-8"), re.S):
            attrs = match.group(1)
            if 'data-busy=""' in attrs:
                continue
            if re.search(r"\btarget\s*=\s*\"_blank\"", attrs):
                uncovered.append((path.name, "target=_blank"))
    assert uncovered == []
