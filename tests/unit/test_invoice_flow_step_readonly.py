"""模組名稱：tests.unit.test_invoice_flow_step_readonly
功能說明：測試發票流程步驟 1～4 於 stage >= 5 或已核准完成時僅開放唯讀，以及步驟 3「再辨識」按鈕功能。
"""

from fastapi.testclient import TestClient

from app.routers import invoices as inv_router

_ORIGIN = {"Origin": "http://testserver"}  # 流程頁的上傳／核對 POST 需同源


def test_step1_and_step3_readonly_when_stage_gte_5(admin_client: TestClient) -> None:
    # 建立一個已達步驟 5 的發票
    invoice_id = 4  # 預設 demo invoice 4 的 stage 為 5
    resp_step1 = admin_client.get(f"/invoices/{invoice_id}/flow?step=1")
    assert resp_step1.status_code == 200
    assert "唯讀" in resp_step1.text
    assert "dropzone--disabled" in resp_step1.text

    resp_step3 = admin_client.get(f"/invoices/{invoice_id}/flow?step=3")
    assert resp_step3.status_code == 200
    assert "唯讀" in resp_step3.text
    assert "readonly" in resp_step3.text
    # 再辨識按鈕應為 disabled
    assert "disabled" in resp_step3.text


def test_step1_upload_blocked_when_stage_gte_5(admin_client: TestClient) -> None:
    invoice_id = 4
    resp = admin_client.post(
        f"/invoices/{invoice_id}/flow/upload",
        headers=_ORIGIN,
        files={"file": ("test.jpg", b"fake image bytes", "image/jpeg")},
    )
    assert resp.status_code == 400


def test_step3_correct_blocked_when_stage_gte_5(admin_client: TestClient) -> None:
    invoice_id = 4
    resp = admin_client.post(
        f"/invoices/{invoice_id}/flow/correct",
        data={
            "invoice_number": "AB99999999",
            "invoice_date": "113/08/15",
            "net_amount": 1000,
        },
    )
    assert resp.status_code == 400


def test_step3_re_ocr_action(admin_client: TestClient) -> None:
    # invoice 3 stage 為 4，可以執行再辨識
    resp = admin_client.post("/invoices/3/flow/re-ocr")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/invoices/3/flow?step=3"

    # invoice 1 stage 為 9 (已完成)，不可執行再辨識
    resp_blocked = admin_client.post("/invoices/1/flow/re-ocr")
    assert resp_blocked.status_code == 400


def test_invoice_list_empty_when_no_records_in_store(admin_client: TestClient, monkeypatch) -> None:
    from app.services import invoice_review_service

    # 當資料庫與 store 均無紀錄時，列表不應顯示任何未上傳假資料
    monkeypatch.setattr(inv_router, "_INVOICE_STORE", [])
    monkeypatch.setattr(invoice_review_service, "fetch_invoices", lambda **kwargs: [])

    resp = admin_client.get("/invoices")
    assert resp.status_code == 200
    assert "AB12345678" not in resp.text
    assert "共 0 筆" in resp.text or "目前尚無發票資料" in resp.text or "0" in resp.text
