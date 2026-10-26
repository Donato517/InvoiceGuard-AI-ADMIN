"""
模組名稱：app.services.user_service
功能說明：使用者管理——開發者帳號（user_accounts）的查詢、新增、修改，以及列表搜尋、啟用 / 停用帳號。
相依模組：app.db.repositories.user_account_repository、app.db.repositories.user_repository、
    app.db.repositories.audit_log_repository
注意事項：
    - 開發者帳號由開發者自行建立與維護，供後續登入功能串接：新帳號即為 active，
      主要部門先使用預設部門，部門功能完成後再以資料更新。
    - 只在雲端（MODULE_BACKEND=cloud）以既有 DB Session 執行；交易由本層 commit，任何失敗都 rollback。
    - 修改帳號需驗證目前密碼，操作者 UUID 取自驗證通過的帳號，不採用前端傳入的身分。
    - 密碼只用來產生或驗證 Argon2id 雜湊，不回傳、不記錄；日誌只記操作者與目標 UUID。
    - 不可停用自己的帳號。
    - 啟用 / 停用必須寫入 audit_logs，且與狀態更新在同一個交易內。
"""

import logging
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.security import hash_password, verify_password
from app.db.repositories import user_account_repository
from app.db.repositories.user_account_repository import AccountStoreError as AccountStoreError
from app.db.repositories.user_account_repository import DuplicateEmailError
from app.schemas.account import AccountSummary

InputCode = Literal["invalid_email", "password_required", "email_taken", "current_password_invalid"]
LIST_LIMIT = 50

logger = logging.getLogger(__name__)


class AccountInputError(ValueError):
    """輸入不符、email 重複或目前密碼錯誤；message 為固定畫面文字，不含密碼。"""

    def __init__(self, code: InputCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def search_accounts(db: Session, query: str = "") -> list[AccountSummary]:
    """查詢帳號：空白列出最近 50 筆；UUID 依 user_id；其餘視為 Email（不分大小寫）完整比對。"""
    keyword = query.strip()
    if not keyword:
        return user_account_repository.list_recent(db, limit=LIST_LIMIT)
    try:
        account = user_account_repository.get(db, UUID(keyword))
    except ValueError:
        account = user_account_repository.find_by_email(db, keyword.lower())
    return [account] if account else []


def create_account(db: Session, *, email: str, password: str) -> AccountSummary:
    """新增 active 帳號（含 profile 與預設主要部門）並在同一交易 commit。

    Raises:
        AccountInputError: email 格式不符、密碼空白或 email 已被使用。
        AccountStoreError: 雲端 DB 沒有可用的帳號資料表或預設部門。
    """
    normalized = _normalize_email(email)
    if not password:
        raise AccountInputError("password_required", "請輸入密碼")
    password_hash = hash_password(password)
    try:
        department_id = user_account_repository.ensure_default_department(db)
        account = user_account_repository.create_active(
            db,
            email=normalized,
            password_hash=password_hash,
            display_name=normalized.partition("@")[0],
            department_id=department_id,
        )
        db.commit()
    except DuplicateEmailError:
        db.rollback()
        raise AccountInputError("email_taken", "此 Email 已被使用") from None
    except Exception:
        db.rollback()
        raise
    logger.info("account_created operator=%s", account.user_id)
    return account


def update_account(
    db: Session,
    user_id: UUID,
    *,
    email: str,
    current_password: str,
    new_password: str | None,
) -> AccountSummary:
    """驗證目前密碼後修改自己的 email；new_password 為 None 或空白表示不變更。成功後 commit。

    Raises:
        AccountInputError: email 格式不符、已被其他帳號使用或目前密碼錯誤。
        NotFoundError: 帳號不存在。
        AccountStoreError: 雲端 DB 沒有可用的帳號資料表。
    """
    normalized = _normalize_email(email)
    password_hash = hash_password(new_password) if new_password else None
    try:
        stored = user_account_repository.get_password_hash(db, user_id)
        if stored is None:
            raise NotFoundError
        if not current_password or not verify_password(current_password, stored):
            raise AccountInputError("current_password_invalid", "目前密碼不正確")
        account = user_account_repository.update(
            db, user_id, email=normalized, password_hash=password_hash
        )
        if account is None:
            raise NotFoundError
        db.commit()
    except DuplicateEmailError:
        db.rollback()
        raise AccountInputError("email_taken", "此 Email 已被使用") from None
    except Exception:
        db.rollback()
        raise
    logger.info("account_updated operator=%s target=%s", user_id, user_id)
    return account


def _normalize_email(email: str) -> str:
    # 與資料表 CHECK 一致：小寫、無前後空白、@ 前後至少一個字元
    normalized = email.strip().lower()
    local, _, domain = normalized.partition("@")
    if not local or not domain:
        raise AccountInputError("invalid_email", "Email 格式不正確")
    return normalized


# TODO(JerryChien): list_users(db, *, keyword, page) -> Page
# TODO(JerryChien): set_user_active(db, user_id, is_active, *, operator, ip_address) -> User
