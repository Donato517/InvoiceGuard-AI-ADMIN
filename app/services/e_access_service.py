"""模組名稱：app.services.e_access_service；功能說明：驗證 E02 投影並決定 E01 工作台資格。"""

from datetime import UTC, datetime
from uuid import UUID

from app.schemas.e_access import (
    AccessSnapshot,
    ActionAccess,
    EffectiveDepartment,
    FunctionAccess,
    NoAdminFunctionsError,
    SelfAccess,
)
from app.schemas.e_admin import Action, DataScope, DeniedError, Identity, Target, UnavailableError
from app.schemas.e_ports import AccessPort
from app.services.e_adapter import call_adapter
from app.services.e_registry import Capability, validate_registry

ACTION_LABELS = {
    Action.VIEW: "查看",
    Action.CREATE: "新增",
    Action.EDIT: "修改",
    Action.DELETE: "刪除",
}


def read_self_access(
    source: AccessPort,
    identity: Identity,
    registry: tuple[Capability, ...],
) -> SelfAccess:
    """向 G 取得當前決策；資料故障及不完整皆拒絕，不回退為僅檢 E 三項功能。"""
    validate_registry(registry)
    snapshot = call_adapter(lambda: source.effective_access(identity))
    return call_adapter(validate_access_snapshot, snapshot, identity, registry)


def validate_access_snapshot(
    snapshot: AccessSnapshot,
    identity: Identity,
    registry: tuple[Capability, ...],
) -> SelfAccess:
    """驗證身分綁定、部門、版本、效期及逐操作完整性；不自行解析 G 的職務政策。"""
    now = datetime.now(UTC)
    if (snapshot.user_id, snapshot.session_id, snapshot.selected_department_id) != (
        identity.user_id,
        identity.session_id,
        identity.selected_department_id,
    ):
        raise DeniedError
    if not identity.admin_access or identity.session_audience != "admin":
        raise DeniedError
    if identity.expires_at.tzinfo is None or identity.expires_at <= now:
        raise DeniedError
    if (
        snapshot.complete is not True
        or not snapshot.policy_version.strip()
        or snapshot.valid_until.tzinfo is None
        or snapshot.valid_until <= now
    ):
        raise UnavailableError
    departments = _departments(snapshot.departments, identity.selected_department_id)
    definitions = {item.code: item for item in registry}
    expected = {(item.code, action) for item in registry for action in item.actions}
    received = set()
    granted: dict[str, list[ActionAccess]] = {item.code: [] for item in registry}
    for decision in snapshot.decisions:
        key = (decision.function_code, decision.action)
        if not isinstance(decision.action, Action) or key not in expected or key in received:
            raise UnavailableError
        received.add(key)
        if (decision.user_id, decision.session_id, decision.selected_department_id) != (
            identity.user_id,
            identity.session_id,
            identity.selected_department_id,
        ):
            raise DeniedError
        if (
            decision.policy_version != snapshot.policy_version
            or decision.valid_until.tzinfo is None
            or decision.valid_until <= now
            or decision.role_effect not in {"allow", "deny", "unset"}
            or not isinstance(decision.function_active, bool)
        ):
            raise UnavailableError
        definition = definitions[decision.function_code]
        scope = decision.scope
        if scope.kind not in {"none", "own", "departments", "company"} or any(
            not isinstance(value, UUID) for value in scope.department_ids
        ):
            raise UnavailableError
        if (
            decision.role_effect != "allow"
            or not decision.function_active
            or scope.kind == "none"
            or (scope.kind == "departments" and not scope.department_ids)
            or (definition.management and not identity.system_management)
        ):
            continue
        if definition.code == "sys.features" and not scope.covers(
            identity.user_id, Target(global_resource=True)
        ):
            continue
        granted[definition.code].append(
            ActionAccess(
                decision.action,
                ACTION_LABELS[decision.action],
                scope,
                _scope_label(scope, departments),
            )
        )
    if received != expected:
        raise UnavailableError
    functions = tuple(
        FunctionAccess(
            item.code,
            item.name,
            item.category,
            item.route.replace("{department_id}", str(identity.selected_department_id)),
            tuple(sorted(granted[item.code], key=lambda grant: grant.action.value)),
        )
        for item in registry
        if granted[item.code]
    )
    if not functions:
        raise NoAdminFunctionsError
    return SelfAccess(
        identity.user_id,
        identity.selected_department_id,
        snapshot.policy_version,
        min(
            snapshot.valid_until,
            identity.expires_at,
            *(decision.valid_until for decision in snapshot.decisions),
        ),
        departments,
        functions,
    )


def _departments(
    values: tuple[EffectiveDepartment, ...],
    selected: UUID,
) -> tuple[EffectiveDepartment, ...]:
    seen = set()
    active = []
    for department in values:
        if (
            not isinstance(department.department_id, UUID)
            or department.department_id in seen
            or not department.department_code.strip()
            or not department.department_name.strip()
            or not isinstance(department.is_active, bool)
        ):
            raise UnavailableError
        seen.add(department.department_id)
        if department.is_active:
            active.append(department)
    if selected not in {department.department_id for department in active}:
        raise DeniedError
    return tuple(active)


def _scope_label(scope: DataScope, departments: tuple[EffectiveDepartment, ...]) -> str:
    labels = {department.department_id: department.department_name for department in departments}
    if scope.kind == "company":
        return "全公司"
    if scope.kind == "own":
        suffix = ""
        if scope.department_ids:
            suffix = (
                "（部門："
                + "、".join(
                    labels.get(value, str(value)) for value in sorted(scope.department_ids, key=str)
                )
                + "）"
            )
        return "本人" + suffix
    return "指定部門：" + "、".join(
        labels.get(value, str(value)) for value in sorted(scope.department_ids, key=str)
    )
