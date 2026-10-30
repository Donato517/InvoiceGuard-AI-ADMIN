"""測試目標：知識庫管理的 E 功能對應、資訊部判斷與 request.state；合成資料，不連 DB。"""

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from app.constants import SESSION_COOKIE_NAME
from app.routers.knowledge import _is_it_department, require_knowledge_access
from app.schemas.e_access import EffectiveDepartment
from app.schemas.e_admin import Action, DeniedError, Target, UnauthenticatedError, UnavailableError
from app.services.admin_login_service import CATALOG_CODES
from app.services.e_admin_service import KNOWLEDGE, get_e_module
from app.services.e_registry import capability, validate_registry

USER = UUID("00000000-0000-4000-8000-000000000001")
IT = UUID("00000000-0000-4000-8000-000000000002")
FINANCE = UUID("00000000-0000-4000-8000-000000000003")
DEPARTMENTS = (
    EffectiveDepartment(IT, "technology_d", "資訊部"),
    EffectiveDepartment(FINANCE, "finance_d", "財務部"),
)


class FakeModule:
    """只記錄 guard 的呼叫；權限判斷本身由 test_department_login 以真實 adapter 測試。"""

    def __init__(self, selected: UUID, *, allowed: bool = True) -> None:
        self.selected = selected
        self.allowed = allowed
        self.checks: list[tuple[Any, ...]] = []

    def identity(self, token: Any) -> SimpleNamespace:
        if token is None:
            raise UnauthenticatedError
        return SimpleNamespace(user_id=USER, selected_department_id=self.selected)

    def check(self, identity: Any, code: str, action: Action, target: Target) -> None:
        self.checks.append((code, action, target))
        if not self.allowed:
            raise DeniedError

    def departments(self, identity: Any) -> tuple[EffectiveDepartment, ...]:
        return DEPARTMENTS

    def sidebar_menu(self, identity: Any) -> tuple[()]:
        return ()


def make_request(*, cookie: bool = True) -> Any:
    cookies = {SESSION_COOKIE_NAME: "synthetic-session"} if cookie else {}
    return SimpleNamespace(cookies=cookies, state=SimpleNamespace())


def test_knowledge_capability_maps_to_existing_catalog_row() -> None:
    registry = get_e_module().registry

    validate_registry(registry)

    definition = capability(KNOWLEDGE, registry)
    assert definition.route == "/knowledge"
    assert definition.actions == frozenset(Action)
    assert definition.system_only is False
    assert CATALOG_CODES[KNOWLEDGE] == "rag_manager"


@pytest.mark.parametrize(
    "code,name,expected",
    [
        ("technology_d", "資訊部", True),
        ("technology_d", "已改名部門", True),
        ("other_d", "資訊部", True),
        ("finance_d", "財務部", False),
    ],
)
def test_it_department_uses_code_or_name(code: str, name: str, expected: bool) -> None:
    departments = (EffectiveDepartment(IT, code, name),)

    assert _is_it_department(departments, IT) is expected


def test_it_membership_only_counts_for_the_selected_department() -> None:
    assert _is_it_department(DEPARTMENTS, FINANCE) is False


def test_access_checks_view_on_selected_department_and_sets_state() -> None:
    module = FakeModule(IT)
    request = make_request()

    require_knowledge_access(request, module)

    assert module.checks == [(KNOWLEDGE, Action.VIEW, Target(IT))]
    assert request.state.knowledge_identity.user_id == USER
    assert request.state.is_it_workspace is True
    assert request.state.header_departments == DEPARTMENTS
    assert request.state.header_selected_department_id == IT
    assert request.state.e_configuration_authenticated is True


def test_access_marks_other_departments_as_non_it() -> None:
    request = make_request()

    require_knowledge_access(request, FakeModule(FINANCE))

    assert request.state.is_it_workspace is False


def test_access_without_cookie_is_unauthenticated() -> None:
    module = FakeModule(IT)

    with pytest.raises(UnauthenticatedError):
        require_knowledge_access(make_request(cookie=False), module)

    assert module.checks == []


def test_denied_department_sets_no_state() -> None:
    request = make_request()

    with pytest.raises(DeniedError):
        require_knowledge_access(request, FakeModule(IT, allowed=False))

    assert not hasattr(request.state, "knowledge_identity")


@pytest.mark.parametrize("stage", ["departments", "sidebar_menu"])
@pytest.mark.parametrize("failure", [DeniedError, UnavailableError])
def test_late_access_failure_publishes_no_partial_state(
    stage: str, failure: type[Exception]
) -> None:
    """讀部門／側欄途中被拒絕或失敗時，不留下已登入旗標或身分。"""
    module = FakeModule(IT)

    def fail(identity: Any) -> Any:
        raise failure

    setattr(module, stage, fail)
    request = make_request()
    with pytest.raises(failure):
        require_knowledge_access(request, module)
    assert vars(request.state) == {}
