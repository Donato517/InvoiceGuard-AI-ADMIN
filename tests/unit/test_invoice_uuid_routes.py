"""
模組名稱：tests.unit.test_invoice_uuid_routes
功能說明：發票路由接受資料庫 UUID 作為 invoice_id，新增發票草稿以 UUID 建立。
"""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.routers import invoices as inv_router

UUID_ID = "5b0f8b0e-6c7a-4c55-9a1e-0a1b2c3d4e5f"


def _uuid_invoice() -> dict:
    invoice = dict(inv_router.DEMO_TEST_INVOICES[2])
    invoice["id"] = UUID_ID
    invoice["uploaded_by"] = "1"
    invoice["stage"] = 4
    return invoice


def test_flow_pages_accept_uuid_invoice_id(admin_client: TestClient) -> None:
    inv_router._INVOICE_STORE.append(_uuid_invoice())
    for step in (1, 2, 3, 4, 5):
        resp = admin_client.get(f"/invoices/{UUID_ID}/flow?step={step}")
        assert resp.status_code == 200, step
    assert admin_client.get(f"/invoices/{UUID_ID}").status_code == 200


def test_uuid_invoice_review_and_readonly_actions(admin_client: TestClient) -> None:
    invoice = _uuid_invoice()
    invoice["stage"] = 5
    inv_router._INVOICE_STORE.append(invoice)
    resp = admin_client.post(f"/invoices/{UUID_ID}/flow/re-ocr")
    assert resp.status_code == 400


def test_new_invoice_draft_uses_uuid_in_production(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(inv_router, "_INVOICE_STORE", [])
    resp = admin_client.get("/invoices/new")
    assert resp.status_code == 303
    new_id = resp.headers["location"].split("/")[2]
    assert str(UUID(new_id)) == new_id
    assert admin_client.get(f"/invoices/{new_id}/flow?step=1").status_code == 200
