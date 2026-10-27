"""模組名稱：app.services.e_authorization；功能說明：供 E／F／G 接線的職務及 scope guard。"""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import SecretStr

from app.schemas.e_admin import (
    Action,
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
    if registered.management and not identity.system_management:
        raise DeniedError
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
    # 職務必須明確允許；部門 can_edit=True 不能補足 unset／衝突。
    if decision.role_effect != "allow" or not decision.function_active:
        raise DeniedError
    if decision.scope.kind == "none":
        raise DeniedError
    if target is not None and not decision.scope.covers(identity.user_id, target):
        raise DeniedError
    return decision
