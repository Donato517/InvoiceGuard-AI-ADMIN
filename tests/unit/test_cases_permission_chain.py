# ruff: noqa: F811
"""測試目標：/cases 經「真實」權限鏈——DepartmentLoginAdapter、EModule、require_permission、網站 Session。

與 test_cases.py 的差別：那邊以假 EModule 驗證路由行為；這裡沿用 test_department_login 的資料庫替身（只替身 SQL
結果，其餘程式為真實），確認功能權限、預設拒絕，以及 E 組 Decision.scope／Target 能否表達「案件所屬部門」。
合成資料，不連 DB；不代表正式權限資料已驗證。
"""

from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

# 沿用登入測試的替身與 fixture（pytest 以 tests/unit 為匯入根）
from test_department_login import (  # noqa: F401
    DEPARTMENT,
    OTHER,
    USER,
    browser,
    encoded_password,
    login,
    module,
    state,
)

from app.schemas.case import CasePage, CaseScope
from app.schemas.e_admin import Action, DeniedError, Target
from app.services import case_service
from app.services.e_admin_service import INVOICES, EModule

CATALOG = "Triplicate Uniform Invoice"  # inv.upload 對應的 app_functions.function_code


def grant(rows: dict[str, Any], **flags: bool) -> None:
    rows["functions"].append(
        {**rows["functions"][0], "function_code": CATALOG, "function_name": "發票上傳", **flags}
    )


@pytest.fixture
def empty_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        case_service,
        "list_cases",
        lambda scope, **kwargs: CasePage((), 0, 1, 20),
    )


class AllowSelectedDepartment:
    """測試用候選政策；正式程式的預設是拒絕。"""

    def scope(self, identity: Any, departments: Any) -> CaseScope:
        return CaseScope("department", identity.selected_department_id)


def test_unauthenticated_is_rejected(browser: TestClient) -> None:
    assert browser.get("/cases").status_code in (303, 401)
    assert browser.get(f"/cases/{USER}/image").status_code in (303, 401)


def test_department_without_the_function_is_denied(
    browser: TestClient,
    state: dict[str, Any],
) -> None:
    login(browser)  # 此部門沒有 Triplicate Uniform Invoice 這一列

    assert browser.get("/cases").status_code == 403


def test_create_edit_flags_do_not_imply_view(
    browser: TestClient,
    state: dict[str, Any],
) -> None:
    grant(state, can_view=False, can_create=True, can_edit=True, can_delete=True)
    login(browser)

    assert browser.get("/cases").status_code == 403


def test_view_permission_alone_still_denied_by_default_case_policy(
    browser: TestClient,
    state: dict[str, Any],
) -> None:
    # 不替身任何 case_service 函式：預設政策必須在開 DB／Storage 之前就拒絕（測試環境禁止連線）
    grant(state, can_view=True, can_create=False, can_edit=False, can_delete=False)
    login(browser)

    for path in ("/cases", f"/cases/{USER}", f"/cases/{USER}/image"):
        assert browser.get(path).status_code == 403, path


def test_with_a_policy_the_real_chain_serves_the_page(
    browser: TestClient,
    state: dict[str, Any],
    empty_cases: None,
) -> None:
    from app.main import app

    grant(state, can_view=True, can_create=False, can_edit=False, can_delete=False)
    login(browser)
    app.dependency_overrides[case_service.get_case_policy] = AllowSelectedDepartment
    try:
        response = browser.get("/cases")
    finally:
        app.dependency_overrides.pop(case_service.get_case_policy, None)

    assert response.status_code == 200


def test_e_decision_scope_can_express_the_case_department(
    module: EModule,
    state: dict[str, Any],
) -> None:
    """E 組既有機制：Decision.scope 為「目前選取部門」，Target 帶案件部門就能分辨跨部門。"""
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.schemas.e_admin import Identity

    grant(state, can_view=True, can_create=False, can_edit=False, can_delete=False)
    identity = Identity(
        USER, uuid4(), DEPARTMENT, 1, datetime.now(UTC) + timedelta(hours=1), True, "admin"
    )

    own = module.check(identity, INVOICES, Action.VIEW, Target(DEPARTMENT, USER))
    assert own.scope.kind == "departments" and own.scope.department_ids == {DEPARTMENT}
    with pytest.raises(DeniedError):  # 案件屬於別的部門
        module.check(identity, INVOICES, Action.VIEW, Target(OTHER, USER))
    with pytest.raises(DeniedError):  # 沒有案件部門資訊就不能視為通過
        module.check(identity, INVOICES, Action.VIEW, Target(None, USER))
    # 目前 /cases 傳的是 Target(identity.selected_department_id)，這一步在該寫法下恆為真：
    module.check(identity, INVOICES, Action.VIEW, Target(identity.selected_department_id))
    _ = UUID  # 保留匯入供型別閱讀
