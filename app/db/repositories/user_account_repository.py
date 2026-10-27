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

from sqlalchemy import TextClause, bindparam, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.exc import SQLAlchemyError as SQLAlchemyError
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

# 正式帳號管理使用獨立查詢，不改動 /dev/accounts 既有查詢契約。
_MANAGEMENT_FILTER = "(strpos(email, :q) > 0 OR CAST(user_id AS text) = :q)"
MANAGEMENT_LIST = text(f"""
    SELECT {_COLUMNS} FROM invoiceguard_identity.user_accounts
    WHERE {_MANAGEMENT_FILTER}
    ORDER BY created_at DESC, user_id LIMIT :limit OFFSET :offset
""")
MANAGEMENT_COUNT = text(f"""
    SELECT count(*) FROM invoiceguard_identity.user_accounts WHERE {_MANAGEMENT_FILTER}
""")
DISABLE_ACCOUNT = text(f"""
    UPDATE invoiceguard_identity.user_accounts SET account_status = :status
    WHERE user_id = :user_id RETURNING {_COLUMNS}
""")
STATUS_CONSTRAINTS = text("""
    SELECT pg_catalog.pg_get_constraintdef(c.oid) AS definition
    FROM pg_catalog.pg_constraint c
    WHERE c.conrelid = 'invoiceguard_identity.user_accounts'::regclass
      AND c.contype = 'c'
      AND EXISTS (
          SELECT 1 FROM pg_catalog.pg_attribute a
          WHERE a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
            AND a.attname = 'account_status'
      )
""")
SELECT_PRIMARY_DEPARTMENT = text("""
    SELECT primary_department_id FROM invoiceguard_identity.user_accounts WHERE user_id = :user_id
""")
LOCK_ACCOUNT_DEPARTMENTS = text("""
    SELECT primary_department_id FROM invoiceguard_identity.user_accounts
    WHERE user_id = :user_id FOR UPDATE
""")
SELECT_MEMBERSHIPS = text("""
    SELECT department_id FROM public.user_departments WHERE user_id = :user_id AND is_current
    ORDER BY department_id
""")
LOCK_SELECTED_DEPARTMENTS = text("""
    SELECT department_id, is_active FROM public.departments
    WHERE department_id IN :department_ids ORDER BY department_id FOR SHARE
""").bindparams(bindparam("department_ids", expanding=True))
SET_PRIMARY_DEPARTMENT = text("""
    UPDATE invoiceguard_identity.user_accounts SET primary_department_id = :department_id
    WHERE user_id = :user_id
""")
REMOVE_MEMBERSHIP = text("""
    UPDATE public.user_departments SET unassigned_at = now()
    WHERE user_id = :user_id AND department_id = :department_id AND unassigned_at IS NULL
""")
ACTIVATE_MEMBERSHIP = text("""
    INSERT INTO public.user_departments (user_id, department_id)
    VALUES (:user_id, :department_id)
    ON CONFLICT (user_id, department_id) DO UPDATE SET unassigned_at = NULL
""")
MEMBERSHIP_FOREIGN_KEYS = text("""
    SELECT ns.nspname AS schema_name, rel.relname AS table_name,
           array_agg(src.attname::text ORDER BY keys.ord) AS source_columns,
           array_agg(dst.attname::text ORDER BY keys.ord) AS target_columns
    FROM pg_catalog.pg_constraint c
    JOIN pg_catalog.pg_class rel ON rel.oid = c.conrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = rel.relnamespace
    CROSS JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY
        AS keys(source_num, target_num, ord)
    JOIN pg_catalog.pg_attribute src ON src.attrelid = c.conrelid AND src.attnum = keys.source_num
    JOIN pg_catalog.pg_attribute dst ON dst.attrelid = c.confrelid AND dst.attnum = keys.target_num
    WHERE c.contype = 'f' AND c.confrelid = 'public.user_departments'::regclass
    GROUP BY c.oid, ns.nspname, rel.relname
""")

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


def list_management(
    db: Session, *, query: str, limit: int, offset: int
) -> tuple[list[AccountSummary], int]:
    """正式列表：Email 部分搜尋或完整 UUID 搜尋，回傳分頁及總數。"""
    total = _run(db, MANAGEMENT_COUNT, {"q": query}).scalar_one()
    rows = _run(db, MANAGEMENT_LIST, {"q": query, "limit": limit, "offset": offset}).mappings()
    return [AccountSummary(**row) for row in rows], total


def supports_disabled_status(db: Session) -> bool:
    """唯讀核對既有 CHECK 契約是否明列 disabled，不猜測其他狀態語意。"""
    definitions = _run(db, STATUS_CONSTRAINTS, {}).mappings()
    return any("'disabled'" in row["definition"] for row in definitions)


def disable_account(db: Session, user_id: UUID) -> AccountSummary | None:
    """停用帳號，保留帳號、密碼、部門關聯與其他資料；不提交交易。"""
    row = (
        _run(db, DISABLE_ACCOUNT, {"user_id": user_id, "status": "disabled"})
        .mappings()
        .one_or_none()
    )
    return AccountSummary(**row) if row else None


def get(db: Session, user_id: UUID) -> AccountSummary | None:
    """依 user_id 取得帳號；不存在時回傳 None。"""
    row = _run(db, SELECT_BY_ID, {"user_id": user_id}).mappings().one_or_none()
    return AccountSummary(**row) if row else None


def get_department_ids(db: Session, user_id: UUID) -> tuple[UUID | None, list[UUID]]:
    """讀取帳號主要部門及全部部門關聯，不讀取或修改權限。"""
    primary = _run(db, SELECT_PRIMARY_DEPARTMENT, {"user_id": user_id}).scalar_one_or_none()
    members = list(_run(db, SELECT_MEMBERSHIPS, {"user_id": user_id}).scalars())
    return primary, members


def lock_department_settings(db: Session, user_id: UUID) -> UUID | None:
    """鎖定帳號以序列化部門設定，鎖逾時不繼續寫入。"""
    _run(db, text("SET LOCAL lock_timeout = '2s'"), {})
    # RLS 隱藏既有關聯時必須拒絕，不能誤認為沒有關聯而覆寫。
    _run(db, text("SET LOCAL row_security = off"), {})
    return _run(db, LOCK_ACCOUNT_DEPARTMENTS, {"user_id": user_id}).scalar_one_or_none()


def lock_selected_departments(db: Session, department_ids: list[UUID]) -> dict[UUID, bool]:
    """鎖定所選部門並讀取狀態，與停用交易互斥。"""
    _run(db, text("SET LOCAL lock_timeout = '2s'"), {})
    rows = _run(db, LOCK_SELECTED_DEPARTMENTS, {"department_ids": department_ids}).mappings()
    return {row["department_id"]: row["is_active"] for row in rows}


def membership_removal_blocker(db: Session) -> str | None:
    """僅允許已知主要部門外鍵；額外外鍵可能連動權限，拒絕移除。"""
    for row in _run(db, MEMBERSHIP_FOREIGN_KEYS, {}).mappings():
        if not (
            row["schema_name"] == "invoiceguard_identity"
            and row["table_name"] == "user_accounts"
            and list(zip(row["source_columns"], row["target_columns"], strict=True))
            in [
                [("user_id", "user_id"), ("primary_department_id", "department_id")],
                [("primary_department_id", "department_id"), ("user_id", "user_id")],
                [
                    ("user_id", "user_id"),
                    ("primary_department_id", "department_id"),
                    ("primary_department_active", "is_current"),
                ],
            ]
        ):
            return (
                "部門關聯有尚未核對的外鍵："
                f"{row['schema_name']}.{row['table_name']}"
                f"（{', '.join(row['source_columns'])} → {', '.join(row['target_columns'])}）；"
                "為避免連動權限資料，未儲存變更"
            )
    return None


def add_membership(db: Session, user_id: UUID, department_id: UUID) -> None:
    """新增或恢復單筆部門關聯；不覆寫現有關聯的其他欄位。"""
    _run(db, ACTIVATE_MEMBERSHIP, {"user_id": user_id, "department_id": department_id})


def set_primary_department(db: Session, user_id: UUID, department_id: UUID) -> None:
    """依管理者明確選擇變更主要部門，不提交交易。"""
    _run(db, SET_PRIMARY_DEPARTMENT, {"user_id": user_id, "department_id": department_id})


def remove_membership(db: Session, user_id: UUID, department_id: UUID) -> None:
    """僅將指定帳號部門關聯設為非目前所屬；保留資料列及其他欄位。"""
    _run(db, REMOVE_MEMBERSHIP, {"user_id": user_id, "department_id": department_id})


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
