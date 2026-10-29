"""模組名稱：app.services.e_configuration_security；功能說明：配置操作沿用目前部門四項許可。

只驗證登入與勾選，不提供管理員指派、角色、交接或額外授權資料寫入。
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.repositories import admin_login_repository as repository
from app.schemas.e_admin import Action, Decision, Identity, Target, UnavailableError
from app.services.admin_login_service import DepartmentLoginAdapter
from app.services.e_authorization import require_permission


@dataclass(frozen=True)
class ConfigurationContext:
    """請求驗證後的操作者及固定操作，不接受表單宣告的身分。"""

    adapter: DepartmentLoginAdapter
    identity: Identity
    requirements: tuple[tuple[str, Action], ...]


_context: ContextVar[ConfigurationContext | None] = ContextVar("e_configuration", default=None)


def enter_configuration(context: ConfigurationContext) -> Token:
    """僅由伺服器路由建立當次請求的上下文。"""
    return _context.set(context)


def leave_configuration(token: Token) -> None:
    """請求結束即還原，不跨使用者保存。"""
    _context.reset(token)


def configuration_active() -> bool:
    """保留本機設定與正式登入流程的判斷，不從環境或 HTTP 宣告角色。"""
    return _context.get() is not None


class _TransactionAccess:
    def __init__(self, context: ConfigurationContext, db: Session) -> None:
        self.context, self.db = context, db

    def decide(self, identity: Identity, code: str, action: Action) -> Decision:
        return self.context.adapter.decision(self.db, identity, code, action)


def authorize_transaction(db: Session) -> bool | None:
    """既有配置操作取鎖後重讀部門勾選，避免畫面檢查後被改權限仍能提交。"""
    context = _context.get()
    if context is None:
        return None
    repository.prepare(db, lock=True)
    actor = context.adapter.current(db, context.identity)
    access = _TransactionAccess(context, db)
    for code, action in context.requirements:
        definition = next((item for item in context.adapter.registry if item.code == code), None)
        if definition is None:
            raise UnavailableError
        require_permission(
            access, actor, code, action, Target(global_resource=True), definition=definition
        )
    return True


def finish_transaction(db: Session, authorized: bool | None) -> None:
    """原交易已取鎖並驗勾選；不新增管理員保護、稽核表或其他資料寫入。"""
    if _context.get() is not None and authorized is not True:
        raise UnavailableError
