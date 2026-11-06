"""測試目標：資料庫案件不得走只改記憶體的舊入口，也不得經舊匯出路徑繞過財務核准。

背景（PR #21 第二輪審查）：procurement/resubmit、flow/upload、import-confirm 等入口對資料庫案件
只會修改一份用完即丟的字典卻回成功；GET /invoices/{id}/export/{kind} 沒有核准檢查。
全部離線，不連資料庫；示範案件的既有展示行為須維持不變。
"""

from collections.abc import Iterator
from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db.repositories import invoice_repository
from app.routers import invoices as inv

DB_ID = "5b0f8b0e-6c7a-4c55-9a1e-0a1b2c3d4e5f"
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def _restore_state() -> Iterator[None]:
    store = deepcopy(inv._INVOICE_STORE)
    yield
    inv._INVOICE_STORE[:] = store
    inv._DRAFT_INVOICE_STORE.clear()


def _make_db_invoice(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> dict[str, Any]:
    invoice = dict(inv.DEMO_TEST_INVOICES[2])
    invoice.update({"id": DB_ID, "uploaded_by": "1", "stage": 3} | overrides)
    monkeypatch.setattr(inv.invoice_review_service, "fetch_invoice", lambda _id: invoice)

    def no_db() -> Any:
        raise AssertionError("不得開啟資料庫 Session")

    monkeypatch.setattr(invoice_repository, "try_get_db_session", no_db)
    return invoice


@pytest.fixture
def pending_db_invoice(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return _make_db_invoice(monkeypatch, review_status="pending", status="passed", stage=5)


@pytest.fixture
def returned_db_invoice(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return _make_db_invoice(monkeypatch, review_status="returned", status="flagged", stage=3)


@pytest.fixture
def approved_db_invoice(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    return _make_db_invoice(
        monkeypatch, review_status="approved", status="passed", stage=9, is_completed=True
    )


# ---- 假成功入口：資料庫案件一律 409，且不改動任何欄位 ----


def test_resubmit_on_a_database_invoice_is_refused(
    admin_client: TestClient, returned_db_invoice: dict[str, Any]
) -> None:
    before = deepcopy(returned_db_invoice)
    inv._FINANCE_CASES.pop(DB_ID, None)  # 其他測試可能留下快取

    response = admin_client.post(f"/invoices/{DB_ID}/procurement/resubmit", data={})

    assert response.status_code == 409
    assert returned_db_invoice == before
    assert DB_ID not in inv._FINANCE_CASES  # 沒有留下假的審核案件快取


def test_attachment_upload_on_a_database_invoice_is_refused(
    admin_client: TestClient, returned_db_invoice: dict[str, Any]
) -> None:
    before = deepcopy(returned_db_invoice)

    response = admin_client.post(
        f"/invoices/{DB_ID}/flow/upload",
        files={"file": ("a.png", b"\x89PNG-bytes", "image/png")},
        headers=ORIGIN,
    )

    assert response.status_code == 409
    assert "尚未支援保存" in response.text or "沒有儲存" in response.text
    assert returned_db_invoice == before  # 沒有把檔案偷放進字典
    assert "file_bytes" not in returned_db_invoice


def test_import_confirm_on_a_database_invoice_is_refused(
    admin_client: TestClient, approved_db_invoice: dict[str, Any]
) -> None:
    response = admin_client.post(f"/invoices/{DB_ID}/import-confirm")

    assert response.status_code == 409
    assert approved_db_invoice.get("import_confirmed_at") is None


def test_complete_on_a_pending_database_invoice_is_refused(
    admin_client: TestClient, pending_db_invoice: dict[str, Any]
) -> None:
    before = deepcopy(pending_db_invoice)

    response = admin_client.post(f"/invoices/{DB_ID}/flow/complete")

    assert response.status_code == 409
    assert pending_db_invoice == before


def test_complete_on_an_approved_database_invoice_changes_nothing(
    admin_client: TestClient, approved_db_invoice: dict[str, Any]
) -> None:
    before = deepcopy(approved_db_invoice)

    response = admin_client.post(f"/invoices/{DB_ID}/flow/complete")

    assert response.status_code == 303 and response.headers["location"] == "/invoices"
    assert approved_db_invoice == before


def test_filing_preview_on_a_pending_database_invoice_is_refused(
    admin_client: TestClient, pending_db_invoice: dict[str, Any]
) -> None:
    response = admin_client.post(f"/invoices/{DB_ID}/filing")

    assert response.status_code == 409
    assert not pending_db_invoice.get("filing_preview")


def test_filing_preview_on_an_approved_database_invoice_is_not_stored(
    admin_client: TestClient, approved_db_invoice: dict[str, Any]
) -> None:
    approved_db_invoice["filing_preview"] = None

    response = admin_client.post(f"/invoices/{DB_ID}/filing")

    assert response.status_code == 303
    assert approved_db_invoice["filing_preview"] is None  # 不產生只存在記憶體的預覽


# ---- 舊匯出：資料庫案件須先核准 ----


@pytest.mark.parametrize("kind", ["report", "input", "voucher"])
def test_legacy_export_refuses_unapproved_database_invoice(
    admin_client: TestClient, pending_db_invoice: dict[str, Any], kind: str
) -> None:
    response = admin_client.get(f"/invoices/{DB_ID}/export/{kind}")

    assert response.status_code == 409
    assert "Content-Disposition" not in response.headers


@pytest.mark.parametrize("kind", ["report", "input", "voucher"])
def test_legacy_export_refuses_returned_database_invoice(
    admin_client: TestClient, returned_db_invoice: dict[str, Any], kind: str
) -> None:
    assert admin_client.get(f"/invoices/{DB_ID}/export/{kind}").status_code == 409


def test_legacy_export_allows_approved_database_invoice(
    admin_client: TestClient, approved_db_invoice: dict[str, Any]
) -> None:
    response = admin_client.get(f"/invoices/{DB_ID}/export/input")

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]


def test_legacy_export_ignores_client_supplied_approval(
    admin_client: TestClient, pending_db_invoice: dict[str, Any]
) -> None:
    """請求內容（查詢字串）不能把待審案件變成已核准。"""
    response = admin_client.get(
        f"/invoices/{DB_ID}/export/input?review_status=approved&status=approved"
    )

    assert response.status_code == 409


def test_legacy_export_requires_finance_workspace(
    monkeypatch: pytest.MonkeyPatch, admin_client: TestClient, approved_db_invoice: dict[str, Any]
) -> None:
    """非財務工作區（最多步驟 4）看不到步驟 9 的匯出。"""
    from fastapi import Request

    from app.main import app

    def limited(request: Request) -> None:
        request.state.invoice_user_id = "1"
        request.state.invoice_max_step = 4
        request.state.is_finance_workspace = False

    app.dependency_overrides[inv.require_invoice_access] = limited

    response = admin_client.get(f"/invoices/{DB_ID}/export/input")

    assert response.status_code == 403


# ---- 示範案件維持原行為 ----


def test_demo_invoice_keeps_import_confirm_and_export(admin_client: TestClient) -> None:
    assert admin_client.post("/invoices/3/import-confirm").status_code == 303
    assert admin_client.get("/invoices/3/export/input").status_code == 200
    assert admin_client.post("/invoices/3/filing").status_code == 303
