"""模組名稱：app.services.department_service
功能說明：部門輸入驗證、停用限制及交易生命週期；不依賴 /dev 模組。
"""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.module_errors import ModuleConfigurationError
from app.db.repositories import department_repository as repository
from app.schemas.department import Department
from app.services.e_configuration_security import authorize_transaction, finish_transaction


class DepartmentError(ValueError):
    """可直接顯示的固定繁體中文錯誤訊息。"""


def _required(value: str, label: str) -> str:
    value = value.strip()
    if not value or "\x00" in value or len(value) > 200:
        raise DepartmentError(f"{label}必須為 1 至 200 字，且不能包含空字元")
    return value


def search(query: str = "") -> list[Department]:
    """讀取真實部門資料，不產生預設或測試資料。"""
    if len(query) > 200 or "\x00" in query:
        raise DepartmentError("搜尋文字不可超過 200 字或包含空字元")
    return _run(lambda db: repository.search(db, query.strip()), write=False)


def create(code: str, name: str) -> Department:
    """驗證後新增部門；代碼唯一性由資料庫保證。"""
    code, name = _required(code, "部門代碼"), _required(name, "部門名稱")
    return _run(lambda db: repository.create(db, code, name))


def rename(department_id: UUID, name: str) -> Department:
    """獨立修改部門名稱，代碼由 change_code 處理。"""
    name = _required(name, "部門名稱")

    def operation(db: Session) -> Department:
        result = repository.rename(db, department_id, name)
        if result is None:
            raise DepartmentError("找不到此部門，請重新整理列表")
        return result

    return _run(operation)


def set_active(department_id: UUID, active: bool) -> Department:
    """停用前檢查預設部門、主要部門、成員關係及外鍵。"""

    def operation(db: Session) -> Department:
        current = repository.lock_department(db, department_id)
        if current is None:
            raise DepartmentError("找不到此部門，請重新整理列表")
        if not active:
            if current.department_code == "UNASSIGNED":
                raise DepartmentError("UNASSIGNED 是新增帳號使用的預設部門，無法停用")
            blocker = repository.disable_blocker(db, department_id)
            if blocker:
                raise DepartmentError(blocker)
        return repository.set_active(db, department_id, active)

    return _run(operation)


def change_code(department_id: UUID, code: str) -> Department:
    """依管理者明確選擇修改代碼，保護預設部門及代碼外鍵。"""
    code = _required(code, "部門代碼")

    def operation(db: Session) -> Department:
        current = repository.lock_department(db, department_id)
        if current is None:
            raise DepartmentError("找不到此部門，請重新整理列表")
        if current.department_code == code:
            return current
        if current.department_code == "UNASSIGNED" or code == "UNASSIGNED":
            raise DepartmentError("UNASSIGNED 為保留的預設部門代碼，無法變更或使用")
        blocker = repository.code_change_blocker(db)
        if blocker:
            raise DepartmentError(blocker)
        return repository.change_code(db, department_id, code)

    return _run(operation)


def _run[T](operation: Callable[[Session], T], *, write: bool = True) -> T:
    try:
        db = repository.open_session()
    except (ModuleConfigurationError, ValueError, repository.SQLAlchemyError):
        raise DepartmentError("資料庫未設定或無法連線，請使用既有 START 啟動 ADMIN") from None
    try:
        security_before = authorize_transaction(db)
        result = operation(db)
        if write:
            finish_transaction(db, security_before)
            db.commit()
        return result
    except repository.IntegrityError as error:
        db.rollback()
        state = getattr(error.orig, "sqlstate", None)
        if state == "23505":
            raise DepartmentError("部門代碼已存在，請使用其他代碼") from None
        raise DepartmentError("資料庫約束拒絕此操作，未儲存變更") from None
    except DepartmentError:
        db.rollback()
        raise
    except repository.SQLAlchemyError:
        db.rollback()
        raise DepartmentError(
            "資料庫查詢失敗或關聯無法確認；未儲存變更，請稍後重試或聯絡維護者"
        ) from None
    finally:
        db.close()
