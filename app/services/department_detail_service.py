"""模組名稱：app.services.department_detail_service；功能說明：本機部門資料與功能配置的單一交易。

這是使用者核定的免登入初始化配置，不代表 G 的主管管理資格或正式 audit 已完成。
"""

import hashlib
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.constants import WELCOME_CODE
from app.db.repositories import department_repository as departments
from app.db.repositories import e_function_settings_repository as permissions
from app.db.repositories.e_repository import safe_store_error
from app.schemas.department_detail import DepartmentDetail, DepartmentDetailInput
from app.schemas.e_admin import (
    Action,
    ConflictError,
    EError,
    MissingError,
    UnavailableError,
    WelcomeReadOnlyError,
)
from app.services.e_adapter import call_adapter
from app.services.e_configuration_security import (
    authorize_transaction,
    configuration_active,
    finish_transaction,
)
from app.services.e_function_settings_service import content_revision


class DetailRuleError(EError):
    """只有伺服器固定規則訊息，不傳底層 SQL 或表單值。"""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__()


def revision(detail: DepartmentDetail) -> str:
    """同時覆蓋部門欄位、功能目錄及四個權限值，避免部分覆寫。"""
    raw = detail.department.model_dump_json() + content_revision(detail.matrix)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def read_detail(department_id: UUID) -> DepartmentDetail:
    """以一致唯讀快照載入完整表單；沒有函式就回空清單，不產生資料。"""

    def operation(db: Session) -> DepartmentDetail:
        if not configuration_active():
            departments.prepare_detail_read(db)
        permissions.prepare_write(db)
        current = departments.get_department(db, department_id)
        if current is None:
            raise MissingError
        return DepartmentDetail(current, permissions.read_matrix(db, department_id))

    return _run(operation)


def save_detail(
    department_id: UUID,
    data: DepartmentDetailInput,
    grants: dict[UUID, frozenset[Action]],
) -> None:
    """基本資料與整頁矩陣一起 CAS；任一失敗都 rollback，未知程式功能只存配置。"""
    if any(not isinstance(action, Action) for actions in grants.values() for action in actions):
        raise EError

    def operation(db: Session) -> None:
        permissions.prepare_write(db, matrix=True)
        current = departments.lock_department(db, department_id)
        if current is None:
            raise MissingError
        matrix = permissions.read_matrix(db, department_id)
        if revision(DepartmentDetail(current, matrix)) != data.expected_revision:
            raise ConflictError
        known = {row.function.function_id: row for row in matrix.rows}
        # 一頁載入全部功能；禁止省略列造成意外撤銷，目錄變更亦需重新載入。
        if set(grants) != set(known):
            raise EError
        for function_id, row in known.items():
            if row.function.function_code == WELCOME_CODE and grants[function_id] != frozenset(
                {Action.VIEW}
            ):
                raise WelcomeReadOnlyError
        if data.department_code != current.department_code:
            if "UNASSIGNED" in {data.department_code, current.department_code}:
                raise DetailRuleError("UNASSIGNED 為保留代碼，不能變更或使用")
            if departments.code_change_blocker(db):
                raise DetailRuleError("部門代碼有外鍵引用，請先由資料庫維護者確認")
        if current.is_active and not data.is_active:
            blocker = departments.disable_blocker(db, department_id)
            if blocker:
                raise DetailRuleError(blocker)
        if (current.department_code, current.department_name, current.is_active) != (
            data.department_code,
            data.department_name,
            data.is_active,
        ):
            departments.update_details(
                db, department_id, data.department_code, data.department_name, data.is_active
            )
        for function_id, actions in grants.items():
            if actions != known[function_id].actions:
                permissions.write_permission(db, department_id, function_id, actions)

    _run(operation, write=True)


def _run[T](operation: Callable[[Session], T], *, write: bool = False) -> T:
    """沿用本站連線與固定錯誤映射，不輸出私人資料。"""
    db = permissions.open_session()
    try:
        security_before = authorize_transaction(db)
        result = operation(db)
        if write:
            finish_transaction(db, security_before)
            db.commit()
        else:
            call_adapter(db.rollback)
        return result
    except Exception as error:
        call_adapter(db.rollback)
        sanitized = safe_store_error(error)
        if sanitized is not None:
            raise sanitized from None
        if isinstance(error, EError):
            raise
        raise UnavailableError from None
    finally:
        call_adapter(db.close)
