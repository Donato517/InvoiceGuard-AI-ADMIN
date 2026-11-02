# ruff: noqa: F811
"""測試目標：/intake 經「真實」權限鏈（DepartmentLoginAdapter、EModule、require_permission）——
功能代碼 inv.intake 沒有註冊在正式功能目錄，即使部門被授予所有功能的全部操作，也一律拒絕（403）。

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


def test_fully_permitted_department_still_cannot_open_intake(
    browser: TestClient, state: dict[str, Any]
) -> None:
    grant_everything(state)
    login(browser)

    assert browser.get("/intake").status_code == 403
    files = {"file": ("a.jpg", b"x", "image/jpeg")}
    assert browser.post("/intake/recognize", headers=ORIGIN, files=files).status_code == 403
    assert (
        browser.post("/intake/review", headers=ORIGIN, data={"action": "submit"}).status_code == 403
    )


def test_unauthenticated_is_rejected(browser: TestClient) -> None:
    assert browser.get("/intake").status_code in (303, 401)
