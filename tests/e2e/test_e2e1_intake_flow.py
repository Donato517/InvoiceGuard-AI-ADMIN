"""E2E-1：ADMIN（真實服務）→ API（真實服務）→ 隔離 PostgreSQL 的上傳、辨識、修正、檢核、提交與讀回。

真實：兩個 HTTP 服務、隔離 PostgreSQL（TLS）、ADMIN Cookie 登入與 Argon2、API 登入與 Session 驗證、API 交易。
替身：Document AI、Groq、Storage（檔案系統）、ADMIN 『新增發票』授權（測試註冊的 inv.intake）、
      ADMIN → API 的憑證（測試專用 Bearer，非 R／P／B 任何方案）。
未驗證：R1 正式認證、真實 AI／Storage、正式 DB 與部署、真實瀏覽器。
"""

import re
import threading
from typing import Any
from uuid import UUID

import pytest
from browser import JPEG, Browser, parse_form, uuid_of

# 團隊契約（API PR #14，2026-11-02 合併）：首次提交直接進「待會計覆核」，不經單位核准。
# 刻意寫死、不從被測 API 的原始碼讀取：API 若改動這個值，此測試必須失敗，由人確認是否為團隊決定。
SUBMITTED_STATUS = "pending_accounting_review"
SUBMITTED_STATUS_LABEL = "待會計覆核"

FIELD_REVIEW_SQL = """
    SELECT r.field_scope, it.line_no, r.field_name, r.ai_original_value, r.submitted_value, r.is_checked
    FROM public.invoice_field_reviews r
    LEFT JOIN public.invoice_items it ON it.item_id = r.item_id
    WHERE r.invoice_id = :i ORDER BY r.field_scope, it.line_no NULLS FIRST, r.field_name
"""


@pytest.fixture
def buyer(stack: Any) -> Browser:
    browser = Browser(stack, "buyer")
    browser.login()
    return browser


def unique_number(stack: Any) -> str:
    """每個案例用不同的發票號碼，避免被查重擋下（模型替身永遠回傳同一個號碼，由『使用者』改成新號碼）。"""
    return f"{stack.scalar('SELECT floor(random()*89999999 + 10000000)::bigint'):08d}"


def recognized_form(
    browser: Browser, stack: Any, number: str | None = None
) -> tuple[dict[str, Any], str]:
    response = browser.recognize()
    assert response.status_code == 200, response.text[:800]
    form = parse_form(response.text)
    if number:
        form["invoice_number"] = number
    return form, response.text


def edited(form: dict[str, Any]) -> dict[str, Any]:
    """人工修正：改賣方名稱、補買受人（含錯誤檢查碼）、改一筆金額、新增一筆明細（AI 沒辨識）。"""
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in form.items()}
    out["seller_name"] = "ABC股份有限公司"
    out["buyer_name"] = "買方測試公司"
    out["buyer_tax_id"] = "12345678"  # 檢查碼不通過：只警示，不阻擋
    out["item_line_amount"][1] = "1100"  # AI 為 1000
    for key, value in (
        ("item_source_line_no", ""),
        ("item_item_name", "運費"),
        ("item_quantity", "1"),
        ("item_unit_price", "100"),
        ("item_line_amount", "100"),
    ):
        out[key].append(value)
    return out


def test_full_flow_upload_to_readback(stack: Any, buyer: Browser) -> None:
    number = unique_number(stack)
    # 1～2. ADMIN 傳圖 → API 辨識（OCR／模型為替身）→ ADMIN 顯示核對頁
    assert buyer.get("/intake").status_code == 200
    form, page = recognized_form(buyer, stack, number)
    assert (form["invoice_track"], form["seller_tax_id"], form["tax_type"]) == (
        "AB",
        "04595257",
        "taxable",
    )
    assert form["buyer_tax_id"] == "" and len(form["item_item_name"]) == 2
    assert form["item_source_line_no"] == ["1", "2"]
    assert "買受人統編" in page  # API 的辨識提醒顯示在頁面
    assert 'src="data:image/jpeg;base64,' in page  # 原始影像隨辨識結果附上
    recognition_id = UUID(form["recognition_id"])

    row = stack.query(
        "SELECT result_status, requested_by, invoice_id, source_storage_path FROM public.ai_recognition_logs WHERE recognition_id = :r",
        r=recognition_id,
    )[0]
    assert row["result_status"] in {"success", "partial"} and row["invoice_id"] is None
    assert str(row["requested_by"]) == stack.users["buyer"]["user_id"]
    stored = stack.work / "storage" / "invoices" / row["source_storage_path"]
    assert stored.read_bytes() == JPEG  # 影像確實經 API 存入（Storage 替身）
    assert len(stack.calls("ocr.process")) >= 1 and len(stack.calls("groq.chat")) >= 1

    # 3～4. 人工修正＋檢核（API 的必填、警示、查重）
    mine = edited(form)
    page = buyer.review("check", mine).text
    assert "買受人統編檢查碼不符" in page or "檢查碼不符" in page
    assert "明細金額合計與銷售額不符" in page
    assert "系統內已有相同字軌" not in page  # 尚未建立，查重為否
    assert 'id="intake-check-results"' in page and 'data-state="checked"' in page
    # 檢核預覽不得建立正式發票或改變辨識紀錄
    assert (
        stack.scalar("SELECT count(*) FROM public.invoices WHERE invoice_number = :n", n=number)
        == 0
    )
    assert stack.scalar(
        "SELECT invoice_id IS NULL FROM public.ai_recognition_logs WHERE recognition_id = :r",
        r=recognition_id,
    )

    # 5. 提交 → 真實 UUID
    done = buyer.review("submit", mine)
    assert done.status_code == 200 and "發票已提交" in done.text, done.text[:1200]
    invoice_id = uuid_of(done.text.split("案件編號")[1])
    assert invoice_id.version == 4

    # 6～7. 資料庫：欄位、原值／修正值、狀態歷程
    inv = stack.query("SELECT * FROM public.invoices WHERE invoice_id = :i", i=invoice_id)[0]
    assert (inv["status"], inv["submission_round"]) == (SUBMITTED_STATUS, 1)
    assert str(inv["submitted_by"]) == stack.users["buyer"]["user_id"]
    assert str(inv["department_id"]) == stack.users["buyer"]["department_id"]
    assert (
        inv["invoice_track"],
        inv["invoice_number"],
        inv["seller_name"],
        inv["buyer_tax_id"],
    ) == ("AB", number, "ABC股份有限公司", "12345678")
    assert (
        str(inv["total_amount"]) == "3150.00"
        and inv["tax_type"] == "taxable"
        and inv["tax_period"] == "202610"
    )
    codes = {w["code"] for w in inv["validation_results"]["warnings"]}
    assert {"tax_id_checksum", "items_total_mismatch"} <= codes
    items = stack.query(
        "SELECT line_no, item_name, line_amount FROM public.invoice_items WHERE invoice_id = :i ORDER BY line_no",
        i=invoice_id,
    )
    assert [(r["line_no"], r["item_name"], str(r["line_amount"])) for r in items] == [
        (1, "辦公文具", "2000.00"),
        (2, "影印紙", "1100.00"),
        (3, "運費", "100.00"),
    ]
    files = stack.query(
        "SELECT storage_path, mime_type, is_current, bucket_name FROM public.invoice_files WHERE invoice_id = :i",
        i=invoice_id,
    )
    assert len(files) == 1 and files[0]["is_current"] and files[0]["bucket_name"] == "invoices"
    assert (
        files[0]["storage_path"] == row["source_storage_path"]
        and files[0]["mime_type"] == "image/jpeg"
    )
    reviews = {
        (r["field_scope"], r["line_no"], r["field_name"]): (
            r["ai_original_value"],
            r["submitted_value"],
            r["is_checked"],
        )
        for r in stack.query(FIELD_REVIEW_SQL, i=invoice_id)
    }
    assert reviews[("header", None, "seller_name")] == ("ABC公司", "ABC股份有限公司", False)
    assert (
        reviews[("header", None, "buyer_name")][0] is None
        and reviews[("header", None, "buyer_name")][1] == "買方測試公司"
    )
    assert reviews[("item", 2, "line_amount")][:2] == ("1000", "1100")
    assert ("item", 3, "item_name") in reviews  # 使用者新增的明細列
    assert ("header", None, "invoice_number") in reviews  # 使用者把號碼改成唯一值
    assert stack.scalar(
        "SELECT count(*) FROM public.invoice_changes WHERE invoice_id = :i", i=invoice_id
    ) >= len(reviews)
    history = stack.query(
        "SELECT old_status, new_status, changed_by FROM public.invoice_status_history WHERE invoice_id = :i",
        i=invoice_id,
    )
    assert [(h["old_status"], h["new_status"], str(h["changed_by"])) for h in history] == [
        (None, SUBMITTED_STATUS, stack.users["buyer"]["user_id"])
    ]
    assert (
        stack.scalar(
            "SELECT invoice_id FROM public.ai_recognition_logs WHERE recognition_id = :r",
            r=recognition_id,
        )
        == invoice_id
    )

    # 8. ADMIN 讀取同一筆案件與圖片（/cases，測試用同部門政策）
    detail = buyer.get(f"/cases/{invoice_id}")
    assert detail.status_code == 200
    for expected in (
        "ABC股份有限公司",
        number,
        "辦公文具",
        "運費",
        SUBMITTED_STATUS_LABEL,
        "買方測試公司",
        "ABC公司",
    ):
        assert expected in detail.text, expected
    assert str(row["source_storage_path"]) not in detail.text
    image = buyer.get(f"/cases/{invoice_id}/image")
    assert (
        image.status_code == 200
        and image.content == JPEG
        and image.headers["content-type"] == "image/jpeg"
    )
    assert str(invoice_id) in buyer.get("/cases").text


def test_unauthenticated_and_unauthorized_are_rejected(stack: Any, buyer: Browser) -> None:
    form, _ = recognized_form(buyer, stack, unique_number(stack))
    done = buyer.review("submit", edited(form))
    invoice_id = uuid_of(done.text.split("案件編號")[1])

    anonymous = Browser(stack, "buyer")  # 沒有登入
    for path in ("/intake", "/cases", f"/cases/{invoice_id}", f"/cases/{invoice_id}/image"):
        assert anonymous.get(path).status_code in {303, 401}, path
    assert anonymous.client.post(
        "/intake/review", headers=anonymous.origin, data={"action": "submit"}
    ).status_code in {303, 401}

    outsider = Browser(stack, "outsider")  # 業務部：沒有新增權限，也不是案件所屬部門
    outsider.login()
    assert outsider.get("/intake").status_code == 403
    assert outsider.recognize().status_code == 403
    assert outsider.review("submit", edited(form)).status_code == 403
    assert outsider.get(f"/cases/{invoice_id}").status_code == 404
    assert outsider.get(f"/cases/{invoice_id}/image").status_code == 404
    assert str(invoice_id) not in outsider.get("/cases").text


def test_resubmitting_the_same_recognition_shows_the_existing_case(
    stack: Any, buyer: Browser
) -> None:
    form, _ = recognized_form(buyer, stack, unique_number(stack))
    first = buyer.review("submit", edited(form))
    invoice_id = uuid_of(first.text.split("案件編號")[1])

    second = buyer.review("submit", edited(form))

    assert (
        second.status_code == 200
        and "沒有重複建立" in second.text
        and str(invoice_id) in second.text
    )
    assert (
        stack.scalar(
            "SELECT count(*) FROM public.invoices WHERE invoice_number = :n",
            n=form["invoice_number"],
        )
        == 1
    )


def test_duplicate_invoice_from_a_new_recognition_is_blocked(stack: Any, buyer: Browser) -> None:
    number = unique_number(stack)
    form_a, _ = recognized_form(buyer, stack, number)
    assert "發票已提交" in buyer.review("submit", edited(form_a)).text

    form_b, _ = recognized_form(buyer, stack, number)  # 另一次辨識、同一張發票
    page = buyer.review("check", edited(form_b)).text
    assert "系統內已有相同字軌" in page  # 查重提示

    blocked = buyer.review("submit", edited(form_b))
    assert (
        blocked.status_code == 400
        and "發票已提交" not in blocked.text
        and "禁止重複" in blocked.text
    )
    assert (
        stack.scalar("SELECT count(*) FROM public.invoices WHERE invoice_number = :n", n=number)
        == 1
    )


def test_submit_response_lost_after_commit_recovers_without_duplicate(
    stack: Any, buyer: Browser
) -> None:
    number = unique_number(stack)
    form, _ = recognized_form(buyer, stack, number)
    stack.set_flag("slow_submit", "6")  # API 寫入完成後延遲 6 秒才回應；ADMIN 的提交逾時為 2 秒

    page = buyer.review("submit", edited(form))

    assert page.status_code == 200 and "沒有重複建立" in page.text
    assert (
        stack.scalar("SELECT count(*) FROM public.invoices WHERE invoice_number = :n", n=number)
        == 1
    )


def test_concurrent_submissions_of_one_recognition_create_one_case(
    stack: Any, buyer: Browser
) -> None:
    number = unique_number(stack)
    form, _ = recognized_form(buyer, stack, number)
    payload = edited(form)
    results: list[Any] = []

    def go() -> None:
        other = Browser(stack, "buyer")
        other.client.cookies = buyer.client.cookies
        results.append(other.review("submit", payload))

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert len(results) == 2 and all(r.status_code == 200 for r in results), [
        r.text[:300] for r in results
    ]
    ids = {uuid_of(r.text.split("案件編號")[1]) for r in results}
    assert len(ids) == 1
    assert (
        stack.scalar("SELECT count(*) FROM public.invoices WHERE invoice_number = :n", n=number)
        == 1
    )


@pytest.mark.parametrize(
    "flag,value,expected_status",
    [
        ("storage_fail", "storage_unreachable", "failed"),
        ("ocr_fail", "document_ai_unreachable", "failed"),
        ("groq_fail", "groq_unreachable", "failed"),
    ],
)
def test_external_service_failure_is_shown_not_hidden(
    stack: Any, buyer: Browser, flag: str, value: str, expected_status: str
) -> None:
    before = stack.scalar("SELECT count(*) FROM public.invoices")
    stack.set_flag(flag, value)

    response = buyer.recognize()

    assert response.status_code in {400, 502} and "核對發票資料" not in response.text
    assert "稍後再試" in response.text or "失敗" in response.text
    last = stack.query(
        "SELECT result_status FROM public.ai_recognition_logs ORDER BY created_at DESC LIMIT 1"
    )[0]
    assert last["result_status"] == expected_status  # 紀錄不會停在 pending
    assert stack.scalar("SELECT count(*) FROM public.invoices") == before


def test_api_down_is_an_error_not_a_success(stack: Any, buyer: Browser) -> None:
    form, _ = recognized_form(buyer, stack, unique_number(stack))
    stack.stop("api")
    try:
        submit = buyer.review("submit", edited(form))
        recognize = buyer.recognize()
    finally:
        stack.start_api()

    assert (
        submit.status_code == 502
        and "發票已提交" not in submit.text
        and "尚未確認" in submit.text
        or "無法連線" in submit.text
    )
    assert recognize.status_code in {400, 502} and "核對發票資料" not in recognize.text
    assert (
        stack.scalar(
            "SELECT count(*) FROM public.invoices WHERE invoice_number = :n",
            n=form["invoice_number"],
        )
        == 0
    )
    # 恢復後同一份表單可以正常送出
    assert "發票已提交" in buyer.review("submit", edited(form)).text


def test_characterization_recognizing_the_same_file_twice_runs_ocr_twice(
    stack: Any, buyer: Browser
) -> None:
    """現況（R1 的檔案雜湊重用尚未實作）：相同檔案連續辨識會重複呼叫 OCR 與模型，並產生兩筆辨識紀錄。"""
    ocr_before = len(stack.calls("ocr.process"))
    rows_before = stack.scalar("SELECT count(*) FROM public.ai_recognition_logs")

    first, second = buyer.recognize(), buyer.recognize()

    assert first.status_code == second.status_code == 200
    assert len(stack.calls("ocr.process")) - ocr_before == 2
    assert stack.scalar("SELECT count(*) FROM public.ai_recognition_logs") - rows_before == 2
    assert parse_form(first.text)["recognition_id"] != parse_form(second.text)["recognition_id"]


def test_oversized_and_non_invoice_files_are_rejected(stack: Any, buyer: Browser) -> None:
    ocr_before = len(stack.calls("ocr.process"))

    too_big = buyer.recognize(b"\xff\xd8\xff" + b"x" * (4 * 1024 * 1024 + 1))
    not_image = buyer.recognize(b"plain text, not an image", "a.txt", "text/plain")

    assert too_big.status_code == 400 and "4 MB" in too_big.text
    assert not_image.status_code == 400 and "核對發票資料" not in not_image.text
    assert len(stack.calls("ocr.process")) == ocr_before


def test_review_page_never_leaks_internal_paths(stack: Any, buyer: Browser) -> None:
    _, page = recognized_form(buyer, stack)

    assert not re.search(r"INVOICES_\d{14}", page)  # Storage 物件名稱
    assert stack.users["buyer"]["user_id"] not in page  # 物件路徑以使用者 UUID 開頭
    assert "storage" not in page.lower() and "bucket" not in page.lower()
