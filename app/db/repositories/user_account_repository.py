"""
模組名稱：app.db.repositories.user_account_repository
功能說明：開發者帳號的查詢、新增與修改——invoiceguard_identity.user_accounts，
    以及 active 帳號必備的 public.user_profiles、public.user_departments 與預設部門。
注意事項：
    - 資料表依 v1.3（結構屬 API 專案）；本站沒有對應 ORM Model，以固定 SQL 存取，值一律綁定參數。
    - 與舊 ORM 的 public.users（BIGINT）無關，不互相引用。
    - 查詢結果只含 user_id、email、account_status；password_hash 只在驗證目前密碼時讀取，不回傳給畫面。
    - 只負責資料存取，不自行 commit／rollback；新增的三筆資料必須由 service 在同一交易提交
      （主要部門外鍵為延遲檢查，提交時才驗證）。
    - email 須由呼叫端先 strip／lower。
"""

from typing import Any, Literal
from uuid import UUID

from sqlalchemy import TextClause, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app.core.module_errors import ModuleConfigurationError
from app.db.session import SessionLocal, get_engine
from app.schemas.account import AccountSummary

# 部門功能完成前，新帳號先掛在此預設部門；之後再以資料更新改為實際部門
DEFAULT_DEPARTMENT_CODE = "UNASSIGNED"
DEFAULT_DEPARTMENT_NAME = "未分配（開發預設）"

_COLUMNS = "user_id, email, account_status"
LIST_RECENT = text(f"""
    SELECT {_COLUMNS} FROM invoiceguard_identity.user_accounts
    ORDER BY created_at DESC LIMIT :limit
""")
SELECT_BY_ID = text(f"""
    SELECT {_COLUMNS} FROM invoiceguard_identity.user_accounts WHERE user_id = :user_id
""")
SELECT_BY_EMAIL = text(f"""
    SELECT {_COLUMNS} FROM invoiceguard_identity.user_accounts WHERE email = :email
""")
SELECT_PASSWORD_HASH = text("""
    SELECT password_hash FROM invoiceguard_identity.user_accounts WHERE user_id = :user_id
""")
ENSURE_DEFAULT_DEPARTMENT = text("""
    INSERT INTO public.departments (department_code, department_name)
    VALUES (:code, :name)
    ON CONFLICT (department_code) DO NOTHING
""")
SELECT_DEFAULT_DEPARTMENT = text("""
    SELECT department_id FROM public.departments WHERE department_code = :code AND is_active
""")
INSERT_ACCOUNT = text(f"""
    INSERT INTO invoiceguard_identity.user_accounts
        (email, password_hash, account_status, primary_department_id)
    VALUES (:email, :password_hash, 'active', :department_id)
    RETURNING {_COLUMNS}
""")
INSERT_PROFILE = text("""
    INSERT INTO public.user_profiles (user_id, display_name) VALUES (:user_id, :display_name)
""")
INSERT_MEMBERSHIP = text("""
    INSERT INTO public.user_departments (user_id, department_id) VALUES (:user_id, :department_id)
""")
# new_password_hash 為 NULL 時保留原值；有變更時由 DB Trigger 遞增憑證版本並撤銷 Session
UPDATE_ACCOUNT = text(f"""
    UPDATE invoiceguard_identity.user_accounts
    SET email = :email, password_hash = COALESCE(:password_hash, password_hash)
    WHERE user_id = :user_id
    RETURNING {_COLUMNS}
""")
EMAIL_UNIQUE_INDEX = "ux_user_accounts_email"

StoreCode = Literal[
    "database_not_configured",
    "account_table_missing",
    "account_schema_mismatch",
    "account_permission_denied",
    "default_department_unavailable",
]
# SQLSTATE → 固定代碼：資料表／schema 不存在、欄位不符、權限（含 GRANT／RLS）不足
_STORE_STATES: dict[str, StoreCode] = {
    "42P01": "account_table_missing",
    "3F000": "account_table_missing",
    "42703": "account_schema_mismatch",
    "42501": "account_permission_denied",
}


class DuplicateEmailError(Exception):
    """email 已被其他帳號使用；交易已失敗，service 須 rollback。"""


class AccountStoreError(RuntimeError):
    """沒有 DB 連線設定，或雲端 DB 沒有可用的帳號資料表／預設部門；只攜帶固定代碼，不含 SQL 或驅動訊息。"""

    def __init__(self, code: StoreCode) -> None:
        self.code = code
        super().__init__(code)


def open_session() -> Session:
    """以既有 DB 模組的 Engine／Session 工廠開啟 Session，呼叫端負責 close。

    Raises:
        AccountStoreError: 程序沒有合法的 DATABASE_URL（database_not_configured）。
    """
    try:
        return SessionLocal(bind=get_engine())
    except (ModuleConfigurationError, ValueError):
        raise AccountStoreError("database_not_configured") from None


def list_recent(db: Session, *, limit: int = 50) -> list[AccountSummary]:
    """依建立時間新到舊列出帳號。"""
    return [AccountSummary(**row) for row in _run(db, LIST_RECENT, {"limit": limit}).mappings()]


def get(db: Session, user_id: UUID) -> AccountSummary | None:
    """依 user_id 取得帳號；不存在時回傳 None。"""
    row = _run(db, SELECT_BY_ID, {"user_id": user_id}).mappings().one_or_none()
    return AccountSummary(**row) if row else None


def find_by_email(db: Session, email: str) -> AccountSummary | None:
    """依已正規化的 email 取得帳號；不存在時回傳 None。"""
    row = _run(db, SELECT_BY_EMAIL, {"email": email}).mappings().one_or_none()
    return AccountSummary(**row) if row else None


def get_password_hash(db: Session, user_id: UUID) -> str | None:
    """取得密碼雜湊，只供 service 驗證目前密碼；帳號不存在時回傳 None。"""
    return _run(db, SELECT_PASSWORD_HASH, {"user_id": user_id}).scalar_one_or_none()


def ensure_default_department(db: Session) -> UUID:
    """取得預設部門 ID；不存在時新增一筆（同一交易）。

    Raises:
        AccountStoreError: 預設部門已存在但被停用。
    """
    params = {"code": DEFAULT_DEPARTMENT_CODE, "name": DEFAULT_DEPARTMENT_NAME}
    _run(db, ENSURE_DEFAULT_DEPARTMENT, params)
    department_id = _run(
        db, SELECT_DEFAULT_DEPARTMENT, {"code": DEFAULT_DEPARTMENT_CODE}
    ).scalar_one_or_none()
    if department_id is None:
        raise AccountStoreError("default_department_unavailable")
    return department_id


def create_active(
    db: Session, *, email: str, password_hash: str, display_name: str, department_id: UUID
) -> AccountSummary:
    """新增 active 帳號，連同 profile 與主要部門成員關係；需由 service 在同一交易提交。

    Raises:
        DuplicateEmailError: email 已存在。
        AccountStoreError: 資料表不存在、欄位不符或權限不足。
    """
    account = AccountSummary(
        **_run(
            db,
            INSERT_ACCOUNT,
            {"email": email, "password_hash": password_hash, "department_id": department_id},
        )
        .mappings()
        .one()
    )
    _run(db, INSERT_PROFILE, {"user_id": account.user_id, "display_name": display_name})
    _run(db, INSERT_MEMBERSHIP, {"user_id": account.user_id, "department_id": department_id})
    return account


def update(
    db: Session, user_id: UUID, *, email: str, password_hash: str | None
) -> AccountSummary | None:
    """修改 email，password_hash 為 None 時不變更密碼；帳號不存在時回傳 None。

    Raises:
        DuplicateEmailError: email 已被其他帳號使用。
        AccountStoreError: 資料表不存在、欄位不符或權限不足。
    """
    params = {"user_id": user_id, "email": email, "password_hash": password_hash}
    row = _run(db, UPDATE_ACCOUNT, params).mappings().one_or_none()
    return AccountSummary(**row) if row else None


def _run(db: Session, statement: TextClause, params: dict[str, Any]) -> Any:
    # 只依約束名稱與 SQLSTATE 轉換；其他錯誤保留原例外，交由 service rollback 與全域錯誤處理
    try:
        return db.execute(statement, params)
    except IntegrityError as error:
        constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
        if constraint == EMAIL_UNIQUE_INDEX:
            raise DuplicateEmailError from None
        raise
    except ProgrammingError as error:
        code = _STORE_STATES.get(getattr(error.orig, "sqlstate", None) or "")
        if code:
            raise AccountStoreError(code) from None
        raise
