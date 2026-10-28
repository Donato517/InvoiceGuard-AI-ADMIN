"""模組名稱：app.services.e_authorization；功能說明：供 E／F／G 接線的職務及 scope guard。"""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import SecretStr

from app.constants import WELCOME_CODE
from app.schemas.e_admin import (
    Action,
    DataScope,
    Decision,
    DeniedError,
    Identity,
    Target,
    UnauthenticatedError,
    UnavailableError,
)
from app.schemas.e_ports import AccessPort
from app.services.e_adapter import call_adapter
from app.services.e_registry import Capability, capability


def verified_identity(access: AccessPort, token: SecretStr | None) -> Identity:
    """僅信任共用認證 adapter，拒絕前台 Session、過期及格式不符的身分。"""
    if token is None or not token.get_secret_value():
        raise UnauthenticatedError
    identity = call_adapter(access.authenticate, token)
    if identity is None:
        raise UnauthenticatedError
    if not all(
        isinstance(value, UUID)
        for value in (
            identity.user_id,
            identity.session_id,
            identity.selected_department_id,
        )
    ):
        raise DeniedError
    if identity.expires_at.tzinfo is None or identity.expires_at <= datetime.now(UTC):
        raise UnauthenticatedError
    if identity.credential_version < 1 or not identity.admin_access:
        raise DeniedError
    if identity.session_audience != "admin":
        raise DeniedError
    return identity


def require_permission(
    access: AccessPort,
    identity: Identity,
    code: str,
    action: Action,
    target: Target | None = None,
    *,
    definition: Capability | None = None,
) -> Decision:
    """每次重新驗權；F／G 可傳入經審查的固定 Capability，不接受 HTTP 定義。

    Args:
        access: 伺服器組裝的權威 adapter。
        identity: 同一次請求透過 verified_identity 取得的身分。
        code: 固定功能代碼。
        action: 固定路由操作。
        target: 由 repository 載入的目標；列表另依回傳 scope 過濾 SQL。
        definition: 其他組程式內的固定 registry 定義，尚需整合註冊。

    Returns:
        綁定該使用者、Session、部門、操作與政策版本的有效決策。
    """
    registered = definition or capability(code)
    if registered.code != code or action not in registered.actions:
        raise DeniedError
    if not identity.admin_access or identity.session_audience != "admin":
        raise DeniedError
    if identity.expires_at.tzinfo is None or identity.expires_at <= datetime.now(UTC):
        raise UnauthenticatedError
    if registered.code == WELCOME_CODE:
        if action != Action.VIEW:
            raise DeniedError
        if identity.credential_version < 1 or not all(
            isinstance(value, UUID)
            for value in (identity.user_id, identity.session_id, identity.selected_department_id)
        ):
            raise DeniedError
        if target is not None and not DataScope("own").covers(identity.user_id, target):
            raise DeniedError
        return Decision(
            identity.user_id,
            identity.session_id,
            identity.selected_department_id,
            WELCOME_CODE,
            Action.VIEW,
            "unset",
            True,
            "builtin-welcome/v1",
            identity.expires_at,
            DataScope("own"),
            department_effect="allow",
        )
    decision = call_adapter(access.decide, identity, code, action)
    if (
        decision.user_id,
        decision.session_id,
        decision.selected_department_id,
        decision.function_code,
        decision.action,
    ) != (
        identity.user_id,
        identity.session_id,
        identity.selected_department_id,
        code,
        action,
    ):
        raise DeniedError
    if not decision.policy_version or decision.valid_until.tzinfo is None:
        raise UnavailableError
    if decision.valid_until <= datetime.now(UTC):
        raise DeniedError
    if not permission_effects_allow(identity, registered, decision):
        raise DeniedError
    if decision.scope.kind not in {"own", "departments", "company"} or (
        decision.scope.kind == "departments" and not decision.scope.department_ids
    ):
        raise DeniedError
    if target is not None and not decision.scope.covers(identity.user_id, target):
        raise DeniedError
    return decision


def permission_effects_allow(
    identity: Identity, definition: Capability, decision: Decision
) -> bool:
    """只依目前部門勾選；不允許額外管理員／職務指派覆蓋部門。"""
    if decision.authorization_basis != "department" or decision.department_effect == "unresolved":
        raise UnavailableError
    return decision.department_effect == "allow" and decision.function_active
