# ruff: noqa: F811
"""測試目標：/intake 經「真實」權限鏈（DepartmentLoginAdapter、EModule、require_permission）——
功能代碼 inv.intake 已註冊在正式功能目錄：未被授予 can_create 的部門一律 403，被授予者轉入發票作業流程 /invoices/new；
呼叫 API 的認證方案未核定，辨識仍為 503（不在此測）。

沿用 test_department_login 的資料庫替身（只替身 SQL 結果）；合成資料，不連 DB；
不代表正式權限資料已驗證。
"""

from typing import Any

from fastapi.testclient import TestClient

# 沿用登入測試的替身與 fixture（pytest 以 tests/unit 為匯入根）
from test_department_login import (  # noqa: F401
    browser,
    encoded_password,
    login,
    module,
    state,
)

CODES = ["Triplicate Uniform Invoice", "inv.intake", "invoices", "inv.upload", "rag_manager"]
ORIGIN = {"Origin": "http://invoiceguard-ai-admin.localhost:8080"}


def grant_everything(rows: dict[str, Any]) -> None:
    for code in CODES:
        rows["functions"].append(
            {
                **rows["functions"][0],
                "function_code": code,
                "function_name": code,
                "app_scope": "both",
                "can_view": True,
                "can_create": True,
                "can_edit": True,
                "can_delete": True,
            }
        )


def test_department_without_intake_grant_is_denied(
    browser: TestClient, state: dict[str, Any]
) -> None:
    login(browser)

    assert browser.get("/intake").status_code == 403
    files = {"file": ("a.jpg", b"x", "image/jpeg")}
    assert browser.post("/intake/recognize", headers=ORIGIN, files=files).status_code == 403
    assert (
        browser.post("/intake/review", headers=ORIGIN, data={"action": "submit"}).status_code == 403
    )


def test_department_with_intake_grant_is_sent_to_the_invoice_flow(
    browser: TestClient, state: dict[str, Any]
) -> None:
    grant_everything(state)
    login(browser)

    response = browser.get("/intake")

    assert response.status_code == 303
    assert response.headers["location"] == "/invoices/new"


def test_unauthenticated_is_rejected(browser: TestClient) -> None:
    assert browser.get("/intake").status_code in (303, 401)


def test_login_stores_the_api_token_in_its_own_httponly_cookie(
    browser: TestClient, monkeypatch: Any
) -> None:
    from app.constants import API_TOKEN_COOKIE_NAME
    from app.services import invoice_intake_service

    monkeypatch.setattr(invoice_intake_service, "obtain_api_token", lambda email, pw: "synthetic")

    login(browser)

    assert browser.cookies.get(API_TOKEN_COOKIE_NAME) == "synthetic"
