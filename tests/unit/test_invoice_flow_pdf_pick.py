# ruff: noqa: F811
"""測試目標：發票流程步驟 1 的多頁 PDF 找發票頁——轉送路由（POST /invoices/pdf-pages/classify）、
新上傳才載入挑頁程式（補附件不拆頁）、API 用戶端送出多張縮圖，以及與前台共用的挑頁 JS 內容一致。

API 以 httpx.MockTransport 的假 API 替身（沿用 test_intake_routes）；合成資料，不連 API、DB 或 Storage。
挑頁流程本身的測試在前台 tests/js/pdf_invoice_page.test.mjs（兩站同一份 JS）。
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from test_intake_routes import FakeApi, api  # noqa: F401

from app.routers import invoices as invoices_router

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT.parent / "InvoiceGuard-AI"
ORIGIN = {"Origin": "http://testserver"}
URL = "/invoices/pdf-pages/classify"
JPEG = b"\xff\xd8\xff\xe0synthetic-jpeg"
CLASSIFIED = {
    "pages": [
        {"index": 1, "is_triplicate_invoice": False, "copy_no": None},
        {"index": 2, "is_triplicate_invoice": True, "copy_no": 2},
    ],
    "model": "gemini-test",
}


def _files(count: int, content: bytes = JPEG) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (f"page_{n}.jpg", content, "image/jpeg")) for n in range(1, count + 1)]


@pytest.fixture(autouse=True)
def _isolated_draft_store() -> Iterator[None]:
    invoices_router._DRAFT_INVOICE_STORE.clear()
    yield
    invoices_router._DRAFT_INVOICE_STORE.clear()


@pytest.fixture
def flow_api(api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    api.routes["/api/v1/invoices/pdf-pages/classify"] = httpx.Response(200, json=CLASSIFIED)
    monkeypatch.setattr(invoices_router, "get_intake_client", lambda request: api.client())
    return api


def test_classify_forwards_page_images_to_api_in_order(
    admin_client: TestClient, flow_api: FakeApi
) -> None:
    response = admin_client.post(URL, headers=ORIGIN, files=_files(2))

    assert response.status_code == 200
    assert response.json() == CLASSIFIED
    assert flow_api.paths() == ["/api/v1/invoices/pdf-pages/classify"]
    body = flow_api.requests[0].content
    assert body.count(b'name="files"') == 2
    assert body.index(b"page_1.jpg") < body.index(b"page_2.jpg")


@pytest.mark.parametrize(
    ("files", "code"),
    [
        (_files(10), "INVOICE_PAGE_COUNT_INVALID"),
        (_files(1, JPEG + b"0" * (1536 * 1024)), "INVOICE_IMAGE_TOO_LARGE"),
        (_files(4, JPEG + b"0" * (1100 * 1024)), "INVOICE_IMAGE_TOO_LARGE"),
    ],
)
def test_classify_rejects_invalid_batches_without_calling_api(
    admin_client: TestClient, flow_api: FakeApi, files: list[Any], code: str
) -> None:
    response = admin_client.post(URL, headers=ORIGIN, files=files)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    assert flow_api.requests == []


def test_classify_passes_api_errors_as_json(admin_client: TestClient, flow_api: FakeApi) -> None:
    flow_api.routes["/api/v1/invoices/pdf-pages/classify"] = httpx.Response(
        502, json={"error": {"code": "AI_PAGE_PICK_FAILED", "message": "AI 無法判斷 PDF 的頁面"}}
    )
    response = admin_client.post(URL, headers=ORIGIN, files=_files(1))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "AI_PAGE_PICK_FAILED"


def test_classify_reports_connection_failure(admin_client: TestClient, flow_api: FakeApi) -> None:
    flow_api.routes["/api/v1/invoices/pdf-pages/classify"] = httpx.ConnectError("down")
    response = admin_client.post(URL, headers=ORIGIN, files=_files(1))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"


def test_classify_requires_same_origin(admin_client: TestClient, flow_api: FakeApi) -> None:
    response = admin_client.post(URL, files=_files(1))
    assert response.status_code == 403
    assert flow_api.requests == []


def test_new_upload_page_loads_pdf_picker(admin_client: TestClient) -> None:
    location = admin_client.get("/invoices/new").headers["location"]
    html = admin_client.get(location).text
    assert 'data-pdf-pick="true"' in html
    assert 'id="pdf-pick-status"' in html
    assert "js/pdf-invoice-page.js" in html and "js/flow-upload-pdf.js" in html
    assert "js/vendor/pdfjs/pdf.min.mjs" in html and "css/pages/pdf-invoice-page.css" in html
    assert "45 頁以內" in html
    assert "Gemini 擷取發票欄位" in html and "Document AI OCR" not in html  # 依現行辨識流程
    # 挑頁模組要在串接程式之前載入，兩者都在 main.js 之後
    assert (
        html.index("main.js") < html.index("pdf-invoice-page.js") < html.index("flow-upload-pdf.js")
    )


def test_existing_invoice_reupload_keeps_the_original_file(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既有案件（清單或資料庫）在步驟 1 補附件時不辨識，PDF 原檔照舊上傳，不載入挑頁程式。"""
    location = admin_client.get("/invoices/new").headers["location"]
    invoice_id = location.split("/")[2]
    monkeypatch.setattr(invoices_router, "_is_new_upload", lambda invoice: False)
    html = admin_client.get(f"/invoices/{invoice_id}/flow?step=1").text
    assert "data-pdf-pick" not in html
    assert "flow-upload-pdf.js" not in html
    assert "PDF（單頁，4 MB 以下）" in html


def test_new_upload_flag_is_false_for_listed_invoices() -> None:
    listed = invoices_router._INVOICE_STORE[0]
    assert invoices_router._is_new_upload(listed) is False


def test_flow_upload_script_intercepts_only_new_pdf_uploads() -> None:
    script = (ROOT / "app/static/js/flow-upload-pdf.js").read_text(encoding="utf-8")
    assert "form.dataset.pdfPick !== 'true'" in script
    assert "'submit'" in script and "true\n  )" in script  # 捕獲階段，先於 main.js
    assert "辨識中…" in script and "button.disabled = true" in script
    assert "alert(" not in script  # 找到或失敗都顯示在畫面上，不跳警示
    # 上傳後蓋全頁遮罩（busy.js），影像上傳也一樣；選擇視窗出現前收到空訊息會先拿掉遮罩
    assert "window.invoiceGuardBusy(message)" in script
    assert "overlay('正在上傳並辨識發票…')" in script
    assert "onProgress: overlay" in script


def test_vendor_pdfjs_and_readme_are_present() -> None:
    vendor = ROOT / "app/static/js/vendor"
    for name in ("pdf.min.mjs", "pdf.worker.min.mjs", "LICENSE"):
        assert (vendor / "pdfjs" / name).is_file()
    assert any((vendor / "pdfjs/cmaps").glob("*.bcmap"))
    readme = (vendor / "README.md").read_text(encoding="utf-8")
    assert "pdfjs-dist" in readme and "4.10.38" in readme


@pytest.mark.skipif(not FRONTEND.is_dir(), reason="需要前台 Repository 在同一層目錄")
@pytest.mark.parametrize(
    "path", ["app/static/js/pdf-invoice-page.js", "app/static/css/pages/pdf-invoice-page.css"]
)
def test_pdf_picker_is_identical_to_frontend(path: str) -> None:
    """挑頁 JS 與樣式兩站共用同一份內容；修改時兩站同步。"""
    assert (ROOT / path).read_bytes() == (FRONTEND / path).read_bytes()
