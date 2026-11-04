"""正式供應商固定參數 SQL；不提交交易、不建立資料表。"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError as SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.repositories.e_repository import open_session as open_session
from app.schemas.e_admin import UnavailableError
from app.schemas.supplier import SupplierInput

COLUMNS = ", ".join(SupplierInput.model_fields)
DISPLAY_COLUMNS = f"supplier_id, {COLUMNS}, is_active, created_at, updated_at"


def prepare(db: Session) -> None:
    """限時且拒絕部分可見資料，避免把 RLS 過濾結果當成全公司主檔。"""
    db.execute(text("SET LOCAL lock_timeout = '2s'"))
    db.execute(text("SET LOCAL statement_timeout = '5s'"))
    if (
        db.execute(
            text("SELECT pg_catalog.row_security_active('public.suppliers'::regclass)")
        ).scalar_one()
        is not False
    ):
        raise UnavailableError


def list_suppliers(db: Session, query: str, offset: int) -> tuple[list[dict], int]:
    """字面搜尋及每頁二十筆；特殊符號不解讀為萬用字元。"""
    condition = "(:q = '' OR strpos(lower(supplier_name), lower(:q)) > 0 OR strpos(lower(tax_id), lower(:q)) > 0 OR strpos(lower(aliases), lower(:q)) > 0)"
    total = db.execute(
        text(f"SELECT count(*) FROM public.suppliers WHERE {condition}"), {"q": query}
    ).scalar_one()
    rows = db.execute(
        text(
            f"SELECT {DISPLAY_COLUMNS} FROM public.suppliers WHERE {condition} ORDER BY supplier_name, supplier_id LIMIT 20 OFFSET :offset"
        ),
        {"q": query, "offset": offset},
    ).mappings()
    return [dict(row) for row in rows], total


def get_supplier(db: Session, supplier_id: UUID) -> dict | None:
    """依 UUID 取得主檔，資料共用不依建立部門裁切。"""
    row = (
        db.execute(
            text(f"SELECT {DISPLAY_COLUMNS} FROM public.suppliers WHERE supplier_id = :id"),
            {"id": supplier_id},
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


def create_supplier(db: Session, values: SupplierInput, actor: UUID) -> UUID:
    """寫入白名單欄位，操作者來自已驗證 Session。"""
    fields = tuple(SupplierInput.model_fields)
    bindings = ", ".join(":" + field for field in fields)
    return db.execute(
        text(
            f"INSERT INTO public.suppliers ({COLUMNS}, created_by, updated_by) VALUES ({bindings}, :actor, :actor) RETURNING supplier_id"
        ),
        values.model_dump() | {"actor": actor},
    ).scalar_one()


def update_supplier(
    db: Session, supplier_id: UUID, values: SupplierInput, revision: object, actor: UUID
) -> bool:
    """原子版本比對防止同時編輯覆蓋；不修改 UUID、狀態或建立時間。"""
    assignments = ", ".join(f"{field} = :{field}" for field in SupplierInput.model_fields)
    return (
        db.execute(
            text(
                f"UPDATE public.suppliers SET {assignments}, updated_by = :actor, updated_at = clock_timestamp() WHERE supplier_id = :id AND updated_at = :revision RETURNING supplier_id"
            ),
            values.model_dump() | {"id": supplier_id, "revision": revision, "actor": actor},
        ).scalar_one_or_none()
        is not None
    )


def set_status(db: Session, supplier_id: UUID, active: bool, revision: object, actor: UUID) -> bool:
    """只啟用或停用，不執行 DELETE。"""
    return (
        db.execute(
            text(
                "UPDATE public.suppliers SET is_active = :active, updated_by = :actor, updated_at = clock_timestamp() WHERE supplier_id = :id AND updated_at = :revision RETURNING supplier_id"
            ),
            {"id": supplier_id, "active": active, "revision": revision, "actor": actor},
        ).scalar_one_or_none()
        is not None
    )
