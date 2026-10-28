"""模組名稱：app.db.repositories.department_repository
功能說明：以固定 SQL 及綁定參數存取 v1.3 部門，交易由 service 控制。
注意事項：不使用舊 users ORM；未知外鍵採拒絕停用，不修改任何關聯。
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError as IntegrityError
from sqlalchemy.exc import SQLAlchemyError as SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_engine
from app.schemas.department import Department

_COLUMNS = "department_id, department_code, department_name, is_active, created_at, updated_at"
SEARCH = text(f"""
    SELECT {_COLUMNS} FROM public.departments
    WHERE strpos(lower(department_code), lower(:q)) > 0
       OR strpos(lower(department_name), lower(:q)) > 0
    ORDER BY department_code, department_id
""")
INSERT = text(f"""
    INSERT INTO public.departments (department_code, department_name)
    VALUES (:code, :name) RETURNING {_COLUMNS}
""")
RENAME = text(f"""
    UPDATE public.departments SET department_name = :name, updated_at = now()
    WHERE department_id = :id RETURNING {_COLUMNS}
""")
STATUS = text(f"""
    UPDATE public.departments SET is_active = :active, updated_at = now()
    WHERE department_id = :id RETURNING {_COLUMNS}
""")
CHANGE_CODE = text(f"""
    UPDATE public.departments SET department_code = :code, updated_at = now()
    WHERE department_id = :id RETURNING {_COLUMNS}
""")
LOCK = text(f"SELECT {_COLUMNS} FROM public.departments WHERE department_id = :id FOR UPDATE")
FOREIGN_KEYS = text("""
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
    WHERE c.contype = 'f' AND c.confrelid = 'public.departments'::regclass
    GROUP BY c.oid, ns.nspname, rel.relname
""")
REFERENCES = text("""
    SELECT EXISTS (
        SELECT 1 FROM invoiceguard_identity.user_accounts WHERE primary_department_id = :id
    ) AS primary_used, EXISTS (
        SELECT 1 FROM public.user_departments WHERE department_id = :id
    ) AS membership_used
""")


def open_session() -> Session:
    """使用既有 Session 工廠開啟連線，由 service 關閉。"""
    return SessionLocal(bind=get_engine())


def search(db: Session, query: str) -> list[Department]:
    """依代碼或名稱做文字搜尋；百分號與底線視為一般字元。"""
    return [Department(**row) for row in db.execute(SEARCH, {"q": query}).mappings()]


def create(db: Session, code: str, name: str) -> Department:
    """新增部門並回傳 DB 實際資料，不提交交易。"""
    return Department(**db.execute(INSERT, {"code": code, "name": name}).mappings().one())


def rename(db: Session, department_id: UUID, name: str) -> Department | None:
    """僅修改名稱，代碼由獨立操作處理。"""
    row = db.execute(RENAME, {"id": department_id, "name": name}).mappings().one_or_none()
    return Department(**row) if row else None


def lock_department(db: Session, department_id: UUID) -> Department | None:
    """鎖住狀態操作目標，避免並行修改。"""
    db.execute(text("SET LOCAL lock_timeout = '2s'"))
    row = db.execute(LOCK, {"id": department_id}).mappings().one_or_none()
    return Department(**row) if row else None


def code_change_blocker(db: Session) -> str | None:
    """如有以代碼而非 UUID 關聯的外鍵，拒絕可能連動其他表的變更。"""
    for row in db.execute(FOREIGN_KEYS).mappings():
        if "department_code" in row["target_columns"]:
            return "部門代碼有其他資料表外鍵引用，請先由資料庫維護者確認，未儲存變更"
    return None


def change_code(db: Session, department_id: UUID, code: str) -> Department:
    """只變更部門代碼及更新時間，保留 UUID、名稱與其他欄位。"""
    return Department(
        **db.execute(CHANGE_CODE, {"id": department_id, "code": code}).mappings().one()
    )


def disable_blocker(db: Session, department_id: UUID) -> str | None:
    """檢查實際外鍵與全部帳號關聯；無法核對的外鍵一律拒絕。

    短交易內 SHARE 鎖防止檢查與停用之間新增或修改關聯；兩秒無法取鎖便失敗回滾。
    不因帳號停用或成員狀態而略過關聯，不刪除或重新分配使用者。
    """
    db.execute(text("SET LOCAL lock_timeout = '2s'"))
    # 若 RLS 會隱藏關聯資料，讓 PostgreSQL 拒絕查詢，不能把不可見誤認為沒有關聯。
    db.execute(text("SET LOCAL row_security = off"))
    db.execute(
        text(
            "LOCK TABLE invoiceguard_identity.user_accounts, public.user_departments IN SHARE MODE"
        )
    )
    allowed = {
        ("invoiceguard_identity", "user_accounts", ("primary_department_id",), ("department_id",)),
        ("public", "user_departments", ("department_id",), ("department_id",)),
    }
    for row in db.execute(FOREIGN_KEYS).mappings():
        key = (
            row["schema_name"],
            row["table_name"],
            tuple(row["source_columns"]),
            tuple(row["target_columns"]),
        )
        if key not in allowed:
            return (
                "資料庫存在尚未核對的部門外鍵關聯："
                f"{row['schema_name']}.{row['table_name']}"
                f"（{', '.join(row['source_columns'])}）；請由資料庫維護者確認後再停用"
            )
    used = db.execute(REFERENCES, {"id": department_id}).mappings().one()
    if used["primary_used"]:
        return "此部門仍是既有帳號的主要部門，無法停用"
    if used["membership_used"]:
        return "此部門仍有使用者部門關係，無法停用"
    return None


def set_active(db: Session, department_id: UUID, active: bool) -> Department:
    """寫入啟用狀態，不提交交易。"""
    return Department(
        **db.execute(STATUS, {"id": department_id, "active": active}).mappings().one()
    )


def get_department(db: Session, department_id: UUID) -> Department | None:
    """單筆內容頁查詢，不取寫入鎖、不讀帳號資料。"""
    row = (
        db.execute(
            text(f"SELECT {_COLUMNS} FROM public.departments WHERE department_id=:id"),
            {"id": department_id},
        )
        .mappings()
        .one_or_none()
    )
    return Department(**row) if row else None


def update_details(db: Session, department_id: UUID, code: str, name: str, active: bool) -> None:
    """同交易一次更新三個部門欄位；呼叫端已鎖列及驗證，不自行提交。"""
    db.execute(
        text("""
            UPDATE public.departments SET department_code=:code, department_name=:name,
                is_active=:active, updated_at=now() WHERE department_id=:id
        """),
        {"id": department_id, "code": code, "name": name, "active": active},
    )


def prepare_detail_read(db: Session) -> None:
    """內容頁以同一個唯讀快照讀取部門及功能，不取寫入鎖。"""
    db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
