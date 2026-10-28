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


def test_knowledge_page_redirects_without_admin(client: TestClient) -> None:
    response = client.get("/knowledge")
    assert response.status_code == 303
    assert "/login?next=" in response.headers["location"]


def test_knowledge_list_renders_for_admin(admin_client: TestClient) -> None:
    response = admin_client.get("/knowledge")
    assert response.status_code == 200
    assert "知識庫管理" in response.text
    assert "營業稅相關規定" in response.text


def test_toggle_kb_department_htmx(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/knowledge/shared-tax/toggle-dept",
        data={"department_id": "finance", "enable": "false"},
    )
    assert response.status_code == 200
    assert "財務部" in response.text


def test_documents_list_renders(admin_client: TestClient) -> None:
    response = admin_client.get("/knowledge/shared-tax/documents")
    assert response.status_code == 200
    assert "tax-guide.pdf" in response.text
    assert "已核准" in response.text


def test_toggle_document_department_htmx(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/knowledge/shared-tax/documents/sample-tax/toggle-dept",
        data={"department_id": "procurement", "enable": "false"},
    )
    assert response.status_code == 200
    assert "採購部" in response.text


def test_document_status_update(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/knowledge/shared-tax/documents/sample-tax/status",
        data={"status": "hold"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/knowledge/shared-tax/documents"


def test_edit_kb_page_and_rename(admin_client: TestClient) -> None:
    response = admin_client.get("/knowledge/shared-tax/edit")
    assert response.status_code == 200
    assert "編輯知識庫" in response.text

    rename_res = admin_client.post(
        "/knowledge/shared-tax/rename",
        data={"title": "營業稅相關規定（新版）"},
    )
    assert rename_res.status_code == 303


def test_document_content_and_download(admin_client: TestClient) -> None:
    content_res = admin_client.get("/knowledge/shared-tax/documents/sample-tax/content")
    assert content_res.status_code == 200
    assert "統一發票應依規定開立與申報" in content_res.text

    download_res = admin_client.get("/knowledge/shared-tax/documents/sample-tax/download")
    assert download_res.status_code == 200
    assert "attachment" in download_res.headers.get("content-disposition", "")


def test_upload_documents_batch(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/knowledge/documents",
        data={
            "department_id": "finance",
            "knowledge_base_id": "shared-tax",
            "status": "hold",
        },
        files=[("files", ("test_policy.txt", b"Test policy content", "text/plain"))],
    )
    assert response.status_code == 200
    assert "test_policy.txt" in response.text


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
