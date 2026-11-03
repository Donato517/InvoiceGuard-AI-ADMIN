"""測試目標：舊的示範核對流程（flow/correct、advance 第 3 步、finish-first-four）不再寫入資料庫。

背景：這條路徑曾用範例預設值組出欄位並 upsert 到 public.invoices，在正式 DB 留下 7 筆假資料。
現在草稿與資料庫發票一律拒絕；程式裡也不再有 save_invoice／UPSERT_INVOICE。全部離線，不連資料庫。
"""

from collections.abc import Iterator
from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db.repositories import invoice_repository
from app.routers import invoices as inv
from app.services import invoice_review_service

ORIGIN = {"Origin": "http://testserver"}
DB_ID = "5b0f8b0e-6c7a-4c55-9a1e-0a1b2c3d4e5f"
CORRECT = {"invoice_number": "CD87654321", "invoice_date": "113/08/20", "net_amount": "10000"}


@pytest.fixture(autouse=True)
def _restore_and_forbid_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    store = deepcopy(inv._INVOICE_STORE)

    def no_db() -> Any:
        raise AssertionError("舊流程不得開啟資料庫 Session")

    monkeypatch.setattr(invoice_repository, "try_get_db_session", no_db)
    yield
    inv._INVOICE_STORE[:] = store
    inv._DRAFT_INVOICE_STORE.clear()


@pytest.fixture
def draft(admin_client: TestClient) -> str:
    response = admin_client.get("/invoices/new")
    assert response.status_code == 303
    return response.headers["location"].split("/")[2]


@pytest.fixture
def db_invoice(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    invoice = dict(inv.DEMO_TEST_INVOICES[2])
    invoice.update(id=DB_ID, uploaded_by="1", stage=4)
    monkeypatch.setattr(inv.invoice_review_service, "fetch_invoice", lambda _id: invoice)
    return invoice


def test_legacy_save_functions_are_gone() -> None:
    assert not hasattr(invoice_review_service, "save_invoice")
    assert not hasattr(invoice_repository, "save_invoice")
    assert not hasattr(invoice_repository, "UPSERT_INVOICE")


def test_correct_on_a_draft_is_refused_and_keeps_the_draft(
    admin_client: TestClient, draft: str
) -> None:
    response = admin_client.post(f"/invoices/{draft}/flow/correct", data=CORRECT)

    assert response.status_code == 409
    assert draft in inv._DRAFT_INVOICE_STORE
    assert all(str(i["id"]) != draft for i in inv._INVOICE_STORE)  # 沒有進入清單


def test_advance_from_step_3_on_a_draft_is_refused(admin_client: TestClient, draft: str) -> None:
    response = admin_client.post(f"/invoices/{draft}/flow/advance", data={"from_step": 3})

    assert response.status_code == 409
    assert draft in inv._DRAFT_INVOICE_STORE


def test_finish_first_four_on_a_draft_is_refused(admin_client: TestClient, draft: str) -> None:
    response = admin_client.post(f"/invoices/{draft}/flow/finish-first-four")

    assert response.status_code == 409


def test_correct_on_a_database_invoice_cannot_overwrite_it(
    admin_client: TestClient, db_invoice: dict[str, Any]
) -> None:
    before = deepcopy(db_invoice)

    response = admin_client.post(f"/invoices/{DB_ID}/flow/correct", data=CORRECT)

    assert response.status_code == 409
    assert db_invoice == before


def test_advance_from_step_3_on_a_database_invoice_is_refused(
    admin_client: TestClient, db_invoice: dict[str, Any]
) -> None:
    response = admin_client.post(f"/invoices/{DB_ID}/flow/advance", data={"from_step": 3})

    assert response.status_code == 409


def test_finish_first_four_on_a_database_invoice_just_returns_to_the_list(
    admin_client: TestClient, db_invoice: dict[str, Any]
) -> None:
    response = admin_client.post(f"/invoices/{DB_ID}/flow/finish-first-four")

    assert response.status_code == 303 and response.headers["location"] == "/invoices"
    assert "first_four_checked" not in db_invoice  # 不改動真實案件


def test_memory_demo_invoices_keep_the_offline_correction_flow(admin_client: TestClient) -> None:
    response = admin_client.post(
        "/invoices/3/flow/correct",
        data=CORRECT | {"seller_tax_id": "24681357", "buyer_tax_id": "12345678"},
    )

    assert response.status_code == 303
