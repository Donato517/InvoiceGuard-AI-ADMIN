"""本公司基本資料固定參數 SQL；表定義見 Schema v2.1（與 migration 8ef487f17b06 相同），固定只有一筆，不提交交易。"""

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError as SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.repositories.e_repository import open_session as open_session
from app.schemas.company_profile import CompanyProfileInput
from app.schemas.e_admin import UnavailableError

COLUMNS = ", ".join(CompanyProfileInput.model_fields)
DISPLAY_COLUMNS = f"{COLUMNS}, created_at, updated_at"


def prepare(db: Session) -> None:
    """限時且拒絕部分可見資料，避免把 RLS 過濾結果當成沒有設定。"""
    db.execute(text("SET LOCAL lock_timeout = '2s'"))
    db.execute(text("SET LOCAL statement_timeout = '5s'"))
    if (
        db.execute(
            text("SELECT pg_catalog.row_security_active('public.company_profile'::regclass)")
        ).scalar_one()
        is not False
    ):
        raise UnavailableError


def get_profile(db: Session) -> dict | None:
    """讀取唯一的一筆；尚未設定時為 None。"""
    row = (
        db.execute(
            text(f"SELECT {DISPLAY_COLUMNS} FROM public.company_profile WHERE profile_id = 1")
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


def create_profile(db: Session, values: CompanyProfileInput, actor: object) -> bool:
    """第一次設定；已有資料（他人先設定）時不覆蓋並回傳 False。"""
    bindings = ", ".join(":" + field for field in CompanyProfileInput.model_fields)
    return (
        db.execute(
            text(
                f"INSERT INTO public.company_profile (profile_id, {COLUMNS}, created_by, updated_by) "
                f"VALUES (1, {bindings}, :actor, :actor) "
                "ON CONFLICT (profile_id) DO NOTHING RETURNING profile_id"
            ),
            values.model_dump() | {"actor": actor},
        ).scalar_one_or_none()
        is not None
    )


def update_profile(
    db: Session, values: CompanyProfileInput, revision: object, actor: object
) -> bool:
    """原子版本比對，避免兩人同時修改互相覆蓋；不修改建立者與建立時間。"""
    assignments = ", ".join(f"{field} = :{field}" for field in CompanyProfileInput.model_fields)
    return (
        db.execute(
            text(
                f"UPDATE public.company_profile SET {assignments}, updated_by = :actor, "
                "updated_at = clock_timestamp() "
                "WHERE profile_id = 1 AND updated_at = :revision RETURNING profile_id"
            ),
            values.model_dump() | {"revision": revision, "actor": actor},
        ).scalar_one_or_none()
        is not None
    )
