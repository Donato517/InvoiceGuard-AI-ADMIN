"""測試目標：頁面路由、管理者登入保護、錯誤頁、靜態檔"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.constants import SESSION_COOKIE_NAME
from app.core import deps
from app.core.config import settings
from app.core.security import create_session_token
from app.db.session import get_db
from app.main import app
from app.services import system_service


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_page_renders(client: TestClient) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert "InvoiceGuard 後台" in response.text


def test_static_css_is_served(client: TestClient) -> None:
    response = client.get("/static/css/main.css")

    assert response.status_code == 200


def test_robots_txt_does_not_block_crawling_so_noindex_is_read(client: TestClient) -> None:
    # 不收錄由頁面 noindex 與 X-Robots-Tag 負責；robots.txt 不封鎖，搜尋引擎才讀得到 noindex。
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert "Allow: /" in response.text


def test_dashboard_redirects_to_login_without_session(client: TestClient) -> None:
    response = client.get("/dashboard")

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fdashboard"


def test_tampered_session_cookie_redirects_to_login(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "SESSION_SECRET_KEY", "test-secret-key-for-unit-tests")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(1) + "tampered")

    response = client.get("/dashboard")

    assert response.status_code == 303


def test_non_admin_user_gets_forbidden(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    normal_user = SimpleNamespace(id=2, display_name="一般使用者", role="user", is_active=True)
    monkeypatch.setattr(deps.user_repository, "get_by_id", lambda db, user_id: normal_user)
    app.dependency_overrides[deps.get_session_user_id] = lambda: 2
    app.dependency_overrides[get_db] = lambda: None
    try:
        response = client.get("/dashboard")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert "沒有後台權限" in response.text


def test_dashboard_renders_for_admin(admin_client: TestClient) -> None:
    response = admin_client.get("/dashboard")

    assert response.status_code == 200
    assert "測試管理者" in response.text


def test_job_runs_page_lists_records(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = SimpleNamespace(
        job_name="sync_einvoice",
        trigger="cron",
        status="failed",
        started_at=datetime(2026, 10, 20, 18, 0, tzinfo=UTC),
        finished_at=datetime(2026, 10, 20, 18, 1, tzinfo=UTC),
        summary=None,
        error_message="財政部平台逾時",
    )

    def fake_list_job_runs(db: Any, *, job_name: str | None = None) -> list[SimpleNamespace]:
        return [run]

    monkeypatch.setattr(system_service, "list_job_runs", fake_list_job_runs)

    response = admin_client.get("/system/job-runs")

    assert response.status_code == 200
    assert "同步財政部電子發票" in response.text
    assert "2026/10/21 02:00" in response.text  # UTC 18:00 → 台灣時間隔天 02:00
    assert "財政部平台逾時" in response.text


def test_unknown_page_shows_custom_404(client: TestClient) -> None:
    response = client.get("/not-exists")

    assert response.status_code == 404
    assert "找不到頁面" in response.text


def test_invalid_form_shows_error_page_without_echoing_input(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/login",
        headers={"Origin": "http://testserver"},
        data={"password": "synthetic-pass-marker"},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "synthetic-pass-marker" not in response.text


def test_invalid_path_parameter_is_not_echoed(admin_client: TestClient) -> None:
    response = admin_client.get("/invoices/synthetic-path-marker")

    assert response.status_code == 400
    assert "synthetic-path-marker" not in response.text


def test_stale_cookie_without_local_session_key_redirects_to_login(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, "stale-synthetic-cookie")

    response = client.get("/dashboard")

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fdashboard"


def test_cloud_session_misconfiguration_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Vercel 上 APP_ENV 保持預設 local → session 設定不合格，不可改為導向登入頁
    monkeypatch.setattr(settings, "VERCEL", "1")
    cloud_client = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    cloud_client.cookies.set(SESSION_COOKIE_NAME, "stale-synthetic-cookie")

    response = cloud_client.get("/dashboard")

    assert response.status_code == 500


def test_login_rejects_cross_site_post(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.com", "password": "x"},
        headers={"Origin": "https://evil.example.com"},
    )

    assert response.status_code == 403


def test_knowledge_page_redirects_without_login(client: TestClient) -> None:
    response = client.get("/knowledge")
    assert response.status_code == 303
    assert "/login?next=" in response.headers["location"]


def test_invoices_list_renders_for_admin(admin_client: TestClient) -> None:
    response = admin_client.get("/invoices")
    assert response.status_code == 200
    assert "發票審核" in response.text
    assert "AB12345678" in response.text


def test_invoice_detail_renders(admin_client: TestClient) -> None:
    response = admin_client.get("/invoices/1")
    assert response.status_code == 200
    assert "AB12345678" in response.text
    assert "6,000" in response.text


def test_invoice_flow_steps_render(admin_client: TestClient) -> None:
    for step in range(4, 10):
        response = admin_client.get(f"/invoices/1/flow?step={step}")
        assert response.status_code == 200
        assert "發票作業流程" in response.text


def test_invoice_review_and_flow_advance(admin_client: TestClient) -> None:
    review_res = admin_client.post(
        "/invoices/1/review",
        data={
            "decision": "passed",
            "purpose": "辦公測試用途",
            "account_code": "5601 辦公用品費",
            "deduction_code": "1",
            "tax_type": "1",
        },
    )
    assert review_res.status_code == 303

    advance_res = admin_client.post(
        "/invoices/1/flow/advance",
        data={"from_step": 4},
    )
    assert advance_res.status_code == 303
    assert advance_res.headers["location"] == "/invoices/1/flow?step=5"


def test_invoice_export(admin_client: TestClient) -> None:
    res_report = admin_client.get("/invoices/1/export/report")
    assert res_report.status_code == 200
    assert "attachment" in res_report.headers.get("content-disposition", "")

    res_input = admin_client.get("/invoices/1/export/input")
    assert res_input.status_code == 200
    assert "發票號碼" in res_input.text

    res_voucher = admin_client.get("/invoices/1/export/voucher")
    assert res_voucher.status_code == 200
    assert "借方金額" in res_voucher.text


def test_new_invoice_only_saved_after_correction(admin_client: TestClient) -> None:
    from app.routers.invoices import _INVOICE_STORE

    initial_ids = {int(i["id"]) for i in _INVOICE_STORE}

    # 1. 建立草稿並僅上傳檔案（尚未完成步驟 3 採購核對修正）
    new_res = admin_client.get("/invoices/new")
    assert new_res.status_code == 303
    assert new_res.headers["location"] == "/invoices/6/flow?step=1"

    upload_res = admin_client.post(
        "/invoices/6/flow/upload",
        files={"file": ("invoice.png", b"fake-png-bytes", "image/png")},
    )
    assert upload_res.status_code == 303
    assert upload_res.headers["location"] == "/invoices/6/flow?step=2"

    # 若未完成採購核對修正即返回發票清單，該筆草稿不應被儲存或顯示於清單
    list_res = admin_client.get("/invoices")
    assert list_res.status_code == 200
    assert "AB12345683" not in list_res.text
    assert {int(i["id"]) for i in _INVOICE_STORE} == initial_ids

    # 2. 重新上傳、經過 AI OCR 辨識並完成採購核對修正後，進度才會正式儲存且清除暫存圖檔
    admin_client.get("/invoices/new")
    admin_client.post(
        "/invoices/6/flow/upload",
        files={"file": ("invoice.png", b"fake-png-bytes", "image/png")},
    )
    admin_client.post("/invoices/6/flow/advance", data={"from_step": 2})
    correct_res = admin_client.post(
        "/invoices/6/flow/correct",
        data={
            "invoice_number": "CD87654321",
            "invoice_date": "113/08/20",
            "seller_tax_id": "24681357",
            "buyer_tax_id": "12345678",
            "item_description": "伺服器機櫃耗材",
            "net_amount": "10000",
        },
    )
    assert correct_res.status_code == 303
    assert correct_res.headers["location"] == "/invoices/6/flow?step=4"

    # 圖檔已清除，不再留存
    file_res = admin_client.get("/invoices/6/file")
    assert file_res.status_code == 404

    # 發票清單已顯示該筆發票，且下次可從步驟 4 繼續辦理
    saved_list_res = admin_client.get("/invoices")
    assert saved_list_res.status_code == 200
    assert "CD87654321" in saved_list_res.text
    assert "/invoices/6/flow?step=4" in saved_list_res.text

    _INVOICE_STORE[:] = [i for i in _INVOICE_STORE if int(i["id"]) in initial_ids]


def test_batch_filing_page_and_export(admin_client: TestClient) -> None:
    # 步驟 1 上傳按鈕無重複，僅保留與 [← 返回列表] 同行之按鈕
    step1_res = admin_client.get("/invoices/1/flow?step=1")
    assert step1_res.status_code == 200
    assert step1_res.text.count("上傳並開始辨識") == 1
    assert 'form="flow-upload-form"' in step1_res.text

    # 步驟 3 左右分欄、14 項核對欄位、無原始圖檔提示，儲存按鈕無重複
    step3_res = admin_client.get("/invoices/1/flow?step=3")
    assert step3_res.status_code == 200
    assert "invoice-correction-split" in step3_res.text
    assert "核對欄位（14 項）" in step3_res.text
    assert "舊資料只有檔名，沒有保存原始檔" in step3_res.text
    assert step3_res.text.count("儲存並進入系統檢核") == 1
    assert 'form="flow-correct-form"' in step3_res.text

    # 步驟 5 財務審核：已核准之發票不應顯示「確認核准」與「退回修正」
    step5_approved = admin_client.get("/invoices/1/flow?step=5")
    assert step5_approved.status_code == 200
    assert "確認核准" not in step5_approved.text
    assert "退回修正" not in step5_approved.text
    assert "此發票已完成審核（已核准）。" in step5_approved.text

    # 步驟 5 財務審核：未核准之發票應顯示「確認核准」與「退回修正」
    step5_pending = admin_client.get("/invoices/3/flow?step=5")
    assert step5_pending.status_code == 200
    assert "確認核准" in step5_pending.text
    assert "退回修正" in step5_pending.text

    # 發票審核詳情（財務審核）顯示 14 項欄位且不含發票原圖
    detail_res = admin_client.get("/invoices/1")
    assert detail_res.status_code == 200
    assert "核對欄位（14 項）" in detail_res.text
    assert "發票聯式／識別" in detail_res.text
    assert "銷貨人" in detail_res.text
    assert "invoice-file-preview" not in detail_res.text
    assert "確認核准" not in detail_res.text
    assert "退回修正" not in detail_res.text

    # 步驟 6 產生申報檔（單張 81 字元預覽 + 批次產生申報檔卡片）
    step6_res = admin_client.get("/invoices/1/flow?step=6")
    assert step6_res.status_code == 200
    assert "本張發票檢核" in step6_res.text
    assert "格式 21 單行預覽（81 字元" in step6_res.text
    assert "批次產生申報檔" in step6_res.text

    # 申報資料頁（批次產生申報檔）
    filing_res = admin_client.get("/invoices/filing")
    assert filing_res.status_code == 200
    assert "批次產生申報檔" in filing_res.text
    assert "已勾選" in filing_res.text
    assert "產生並下載申報檔" in filing_res.text

    # 示範發票的統編檢查碼與字軌未通過 F09 檢核：單張顯示原因、不產生預覽，批次整批不產檔
    assert "不可列入申報" in step6_res.text
    assert "銷售人統編未通過統一編號邏輯檢查" in step6_res.text
    assert '<pre class="filing-line">' not in step6_res.text
    export_res = admin_client.post(
        "/invoices/filing/export",
        data={"period": "11308", "invoice_ids": [1, 2, 5]},
    )
    assert export_res.status_code == 400
    assert "content-disposition" not in export_res.headers


def test_batch_filing_exports_valid_invoice_with_f09(admin_client: TestClient) -> None:
    from app.routers.invoices import _INVOICE_STORE

    initial_ids = {int(i["id"]) for i in _INVOICE_STORE}
    valid = {
        "id": 901,
        "invoice_number": "EC08904069",
        "invoice_date": "115/09/15",
        "seller_tax_id": "27601907",
        "buyer_tax_id": "04595252",
        "net_amount": 6000,
        "tax_amount": 300,
        "tax_category": "應稅",
        "deduction_code": "1 - 進貨及費用",
        "review_status": "approved",
        "status": "passed",
        "stage": 9,
        "is_completed": True,
    }
    _INVOICE_STORE.append(valid)
    try:
        page = admin_client.get(
            "/invoices/filing",
            params={"period": "11510", "from_date": "2026-09-01", "to_date": "2026-10-31"},
        )
        assert page.status_code == 200
        assert "EC08904069" in page.text
        assert "AB12345678" not in page.text  # 113 年示範發票不在起訖日內
        assert "04595252.TXT" in page.text
        assert "28／29 欄 $6,000／$300" in page.text

        export = admin_client.post(
            "/invoices/filing/export", data={"period": "11510", "invoice_ids": [901]}
        )
        assert export.status_code == 200
        assert "04595252.TXT" in export.headers["content-disposition"]
        (line,) = [ln for ln in export.text.split("\r\n") if ln]
        assert len(line) == 81
        assert line.startswith("21123456789000000111510")

        mixed = admin_client.post(
            "/invoices/filing/export", data={"period": "11510", "invoice_ids": [901, 1]}
        )
        assert mixed.status_code == 400
    finally:
        _INVOICE_STORE[:] = [i for i in _INVOICE_STORE if int(i["id"]) in initial_ids]


def test_batch_filing_rejects_bad_period(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/invoices/filing/export", data={"period": "113", "invoice_ids": [1]}
    )

    assert response.status_code == 400
