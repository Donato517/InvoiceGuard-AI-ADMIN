"""模組名稱：app.db.repositories.admin_login_repository；功能說明：登入讀取既有帳號、部門及功能勾選。

只使用 v1.3 既有欄位；登入唯一寫入為 last_login_at，不建立 Session、角色或授權資料。
"""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.e_admin import UnauthenticatedError, UnavailableError

TABLES = (
    "invoiceguard_identity.user_accounts",
    "public.user_profiles",
    "public.user_departments",
    "public.departments",
    "public.app_functions",
    "public.department_permissions",
)


def prepare(db: Session, *, lock: bool = False) -> None:
    """保留短查詢與完整可見性；配置交易取鎖後驗權，不新增資料或繞過 RLS。"""
    db.execute(text("SET LOCAL statement_timeout = '5s'"))
    db.execute(text("SET LOCAL lock_timeout = '2s'"))
    if lock:
        db.execute(text("LOCK TABLE " + ",".join(TABLES) + " IN SHARE ROW EXCLUSIVE MODE"))
    for name in TABLES:
        if (
            db.execute(
                text("SELECT pg_catalog.row_security_active(CAST(:name AS regclass))"),
                {"name": name},
            ).scalar_one()
            is not False
        ):
            raise UnavailableError


def login_account(db: Session, email: str) -> dict[str, Any] | None:
    """只有驗帳密時讀雜湊；正規化 Email 由 service 傳入，值採綁定參數。"""
    row = (
        db.execute(
            text("""
        SELECT a.user_id,a.email,a.password_hash,a.account_status,a.credential_version,
          a.locked_until,a.primary_department_id,p.is_active AS profile_active,p.resigned_at
        FROM invoiceguard_identity.user_accounts a
        JOIN public.user_profiles p USING(user_id) WHERE a.email=:email
    """),
            {"email": email},
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


def current_account(db: Session, user_id: UUID) -> dict[str, Any] | None:
    """驗網站 Session 所屬帳號的當前有效性；不讀密碼雜湊。"""
    row = (
        db.execute(
            text("""
        SELECT a.user_id,a.account_status,a.credential_version,a.locked_until,
          a.primary_department_id,p.is_active AS profile_active,p.resigned_at
        FROM invoiceguard_identity.user_accounts a
        JOIN public.user_profiles p USING(user_id) WHERE a.user_id=:user_id
    """),
            {"user_id": user_id},
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


def departments(db: Session, user_id: UUID) -> tuple[dict[str, Any], ...]:
    """回傳本人仍有效的部門；不合併各部門的功能勾選。"""
    return tuple(
        dict(row)
        for row in db.execute(
            text("""
        SELECT d.department_id,d.department_code,d.department_name,d.is_active
        FROM public.user_departments ud JOIN public.departments d USING(department_id)
        WHERE ud.user_id=:user_id AND ud.unassigned_at IS NULL AND d.is_active
        ORDER BY d.department_code,d.department_id
    """),
            {"user_id": user_id},
        ).mappings()
    )


def functions(db: Session, user_id: UUID, department_id: UUID) -> tuple[dict[str, Any], ...]:
    """以單一查詢取得目前所屬有效部門的設定；四操作獨立、未勾選就是 false。"""
    return tuple(
        dict(row)
        for row in db.execute(
            text("""
        SELECT f.function_code,f.function_name,f.app_scope,f.is_active,
          COALESCE(p.can_view,false) AS can_view,COALESCE(p.can_create,false) AS can_create,
          COALESCE(p.can_edit,false) AS can_edit,COALESCE(p.can_delete,false) AS can_delete
        FROM public.user_departments ud JOIN public.departments d USING(department_id)
        CROSS JOIN public.app_functions f
        LEFT JOIN public.department_permissions p
          ON p.department_id=d.department_id AND p.function_id=f.function_id
        WHERE ud.user_id=:user_id AND ud.department_id=:department_id
          AND ud.unassigned_at IS NULL AND d.is_active ORDER BY f.function_code
    """),
            {"user_id": user_id, "department_id": department_id},
        ).mappings()
    )


def update_login_record(db: Session, user_id: UUID, version: int) -> None:
    """登入成功只更新現有登入時間；不修改錯誤次數、帳號狀態、密碼、權限或 Session 表。"""
    updated = db.execute(
        text("""
        UPDATE invoiceguard_identity.user_accounts SET last_login_at=clock_timestamp()
        WHERE user_id=:user_id AND credential_version=:version AND account_status='active'
        RETURNING user_id
    """),
        {"user_id": user_id, "version": version},
    ).scalar_one_or_none()
    if updated is None:
        raise UnauthenticatedError


def profile(db: Session, user_id: UUID) -> dict[str, Any]:
    """本人資訊白名單，不輸出認證雜湊。"""
    return dict(
        db.execute(
            text("""
        SELECT a.user_id,a.email,p.display_name FROM invoiceguard_identity.user_accounts a
        JOIN public.user_profiles p USING(user_id) WHERE a.user_id=:user_id
    """),
            {"user_id": user_id},
        )
        .mappings()
        .one()
    )
