"""模組名稱：app.services.account_management_service
功能說明：正式帳號管理，列表、新增、管理者直接修改及停用，交易失敗回滾。
注意事項：沿用既有 v1.3 Repository、密碼雜湊與新增帳號流程，不依賴 /dev。
"""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.repositories import department_repository
from app.db.repositories import user_account_repository as repository
from app.schemas.account import AccountSummary
from app.schemas.account_management import AccountDepartmentSettings
from app.services import user_service

PAGE_SIZE = 20


class AccountManagementError(ValueError):
    """可直接呈現在帳號頁面的固定錯誤訊息，不含 DB 或密碼細節。"""


def list_accounts(query: str = "", page: int = 1) -> tuple[list[AccountSummary], int]:
    """依 Email 或 UUID 搜尋並分頁，不回傳密碼雜湊。"""
    if len(query) > 320 or "\x00" in query or not 1 <= page <= 1000000:
        raise AccountManagementError("搜尋文字或頁碼不正確")
    return _run(
        lambda db: repository.list_management(
            db, query=query.strip().lower(), limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
        )
    )


def get_account(user_id: UUID) -> AccountSummary:
    """取得內容頁資料；不存在時回傳明確訊息。"""
    return _run(lambda db: _found(repository.get(db, user_id)))


def create_account(
    email: str,
    password: str,
    *,
    department_ids: list[UUID] | None = None,
    primary_department_id: UUID | None = None,
) -> AccountSummary:
    """重用既有建立帳號、profile 與預設部門的同一交易流程。"""
    email = _email(email)
    _password(password, required=True)
    if department_ids is not None:
        selected = _department_selection(department_ids, primary_department_id)
        password_hash = hash_password(password)

        def operation(db: Session) -> AccountSummary:
            _validate_departments(db, selected, set(), primary_department_id, None)
            account = repository.create_active(
                db,
                email=email,
                password_hash=password_hash,
                display_name=email.partition("@")[0],
                department_id=primary_department_id,
            )
            for department_id in selected:
                if department_id != primary_department_id:
                    repository.add_membership(db, account.user_id, department_id)
            return account

        return _run(operation, write=True)
    return _run(lambda db: user_service.create_account(db, email=email, password=password))


def get_department_settings(user_id: UUID | None = None) -> AccountDepartmentSettings:
    """取得內容頁選項；新增預選啟用中的 UNASSIGNED（若存在）。"""

    def operation(db: Session) -> AccountDepartmentSettings:
        departments = department_repository.search(db, "")
        if user_id is not None:
            _found(repository.get(db, user_id))
            primary, selected = repository.get_department_ids(db, user_id)
        else:
            primary = next(
                (
                    d.department_id
                    for d in departments
                    if d.department_code == "UNASSIGNED" and d.is_active
                ),
                None,
            )
            selected = [primary] if primary else []
        return AccountDepartmentSettings(
            departments=departments, selected_ids=selected, primary_department_id=primary
        )

    return _run(operation)


def save_department_settings(
    user_id: UUID, department_ids: list[UUID], primary_department_id: UUID | None
) -> None:
    """原子儲存多部門與主要部門；保留未變動的關聯及其附加資料。"""
    selected = _department_selection(department_ids, primary_department_id)

    def operation(db: Session) -> None:
        old_primary = repository.lock_department_settings(db, user_id)
        _found(repository.get(db, user_id))
        _, existing = repository.get_department_ids(db, user_id)
        existing_ids = set(existing)
        _validate_departments(db, selected, existing_ids, primary_department_id, old_primary)
        removed = existing_ids - set(selected)
        if removed:
            blocker = repository.membership_removal_blocker(db)
            if blocker:
                raise AccountManagementError(blocker)
        for department_id in selected:
            if department_id not in existing_ids:
                repository.add_membership(db, user_id, department_id)
        repository.set_primary_department(db, user_id, primary_department_id)
        for department_id in sorted(removed):
            repository.remove_membership(db, user_id, department_id)

    _run(operation, write=True)


def _department_selection(department_ids: list[UUID], primary: UUID | None) -> list[UUID]:
    selected = sorted(set(department_ids))
    if not selected or len(selected) > 200:
        raise AccountManagementError("請選擇至少一個所屬部門，且不可超過 200 個")
    if primary is None or primary not in selected:
        raise AccountManagementError("主要部門必須是已勾選的所屬部門")
    return selected


def _validate_departments(
    db: Session,
    selected: list[UUID],
    existing: set[UUID],
    primary: UUID | None,
    old_primary: UUID | None,
) -> None:
    states = repository.lock_selected_departments(db, selected)
    if set(states) != set(selected):
        raise AccountManagementError("所選部門不存在或已移除，請重新整理頁面")
    if any(
        not active and department_id not in existing for department_id, active in states.items()
    ):
        raise AccountManagementError("不可新增指派已停用的部門")
    if primary != old_primary and not states[primary]:
        raise AccountManagementError("不可將已停用的部門設為新的主要部門")


def update_account(user_id: UUID, email: str, new_password: str = "") -> AccountSummary:
    """依使用者授權由管理者直接修改，留空新密碼保留原雜湊。"""
    email = _email(email)
    _password(new_password, required=False)
    password_hash = hash_password(new_password) if new_password else None
    return _run(
        lambda db: _found(repository.update(db, user_id, email=email, password_hash=password_hash)),
        write=True,
    )


def disable_account(user_id: UUID) -> AccountSummary:
    """刪除採停用且保留所有資料；先核對真實資料庫的狀態契約。"""

    def operation(db: Session) -> AccountSummary:
        _found(repository.get(db, user_id))
        if not repository.supports_disabled_status(db):
            raise AccountManagementError(
                "資料庫尚未確認 disabled 停用狀態，未變更帳號；請由資料庫維護者核對狀態契約"
            )
        return _found(repository.disable_account(db, user_id))

    return _run(operation, write=True)


def _found(account: AccountSummary | None) -> AccountSummary:
    if account is None:
        raise AccountManagementError("找不到此帳號，請返回列表重新查詢")
    return account


def _email(value: str) -> str:
    value = value.strip().lower()
    local, separator, domain = value.partition("@")
    if (
        not local
        or not separator
        or not domain
        or "@" in domain
        or len(value) > 320
        or any(c.isspace() or ord(c) < 32 for c in value)
    ):
        raise AccountManagementError("Email 格式不正確")
    return value


def _password(value: str, *, required: bool) -> None:
    if (required and not value) or len(value) > 256 or "\x00" in value:
        raise AccountManagementError("密碼為必填且不可超過 256 字；修改時可留空保留原密碼")


def _run[T](operation: Callable[[Session], T], *, write: bool = False) -> T:
    try:
        db = repository.open_session()
    except (repository.AccountStoreError, repository.SQLAlchemyError):
        raise AccountManagementError(
            "資料庫未設定或無法連線，請使用既有 START 啟動 ADMIN"
        ) from None
    try:
        result = operation(db)
        if write:
            db.commit()
        return result
    except (repository.DuplicateEmailError, user_service.AccountInputError) as error:
        db.rollback()
        message = (
            "此 Email 已被使用" if isinstance(error, repository.DuplicateEmailError) else str(error)
        )
        raise AccountManagementError(message) from None
    except AccountManagementError:
        db.rollback()
        raise
    except repository.AccountStoreError:
        db.rollback()
        raise AccountManagementError(
            "帳號資料表、預設部門或資料庫權限尚未就緒，未儲存變更"
        ) from None
    except repository.SQLAlchemyError:
        db.rollback()
        raise AccountManagementError("資料庫拒絕操作或連線失敗，未儲存變更，請稍後重試") from None
    finally:
        db.close()
