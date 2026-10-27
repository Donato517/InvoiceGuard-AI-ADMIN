"""使用者核定本機免登入後，雲端入口仍拒絕；不連正式 DB。"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services import account_management_service, department_service

ACCOUNT = "00000000-0000-4000-8000-000000000001"
DEPARTMENT = "00000000-0000-4000-8000-000000000002"
BASE = "http://invoiceguard-ai-admin.localhost:8080"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/accounts"),
        ("GET", "/accounts/new"),
        ("GET", f"/accounts/{ACCOUNT}/edit"),
        ("POST", "/accounts/new"),
        ("POST", f"/accounts/{ACCOUNT}/edit"),
        ("POST", f"/accounts/{ACCOUNT}/disable"),
        ("POST", f"/accounts/{ACCOUNT}/departments"),
        ("GET", "/department"),
        ("POST", "/department"),
        ("POST", f"/department/{DEPARTMENT}/name"),
        ("POST", f"/department/{DEPARTMENT}/code"),
        ("POST", f"/department/{DEPARTMENT}/status"),
    ],
)
def test_incoming_routes_stay_closed_in_cloud_after_local_exception(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    calls = []
    for service, names in [
        (
            account_management_service,
            [
                "list_accounts",
                "get_account",
                "get_department_settings",
                "create_account",
                "update_account",
                "disable_account",
                "save_department_settings",
            ],
        ),
        (department_service, ["search", "create", "rename", "change_code", "set_active"]),
    ]:
        for name in names:
            spy = MagicMock(side_effect=AssertionError("封閉入口不得呼叫業務或 DB"))
            monkeypatch.setattr(service, name, spy)
            calls.append(spy)
    with TestClient(
        app, base_url=BASE, client=("127.0.0.1", 50000), follow_redirects=False
    ) as browser:
        response = browser.request(
            method, path, headers={"Origin": BASE, "X-Role": "admin", "X-User-ID": ACCOUNT}
        )
    assert response.status_code == 404
    assert "set-cookie" not in response.headers
    for spy in calls:
        spy.assert_not_called()
