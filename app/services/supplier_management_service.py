"""正式共用供應商交易；沿用部門授權並於同一交易重新驗證。"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.repositories import suppliers_repository as repository
from app.schemas.e_admin import EError, MissingError, UnavailableError
from app.schemas.supplier import SupplierInput
from app.services.e_configuration_security import (
    authorize_transaction,
    configuration_active,
    finish_transaction,
)


class SupplierRuleError(Exception):
    """供應商表單可呈現的固定業務錯誤。"""


class SupplierUnavailableError(UnavailableError):
    """不公開底層連線或 SQL 的正式供應商錯誤。"""

    message = "供應商資料暫時無法讀寫，請確認資料庫結構與服務權限已完成設定後重試"


def revision_value(value: str) -> datetime:
    """拒絕缺少時區的版本，避免伺服器時區造成誤判。"""
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is not None:
            return result
    except ValueError:
        pass
    raise SupplierRuleError("資料版本無效，請重新開啟畫面")


def _display(row: dict) -> dict:
    for name in ("created_at", "updated_at"):
        row[name] = row[name].astimezone(ZoneInfo("Asia/Taipei")).isoformat()
    row["supplier_id"] = str(row["supplier_id"])
    return row


def list_suppliers(query: str, page: int) -> tuple[list[dict], int]:
    """讀取真實主檔，不產生預設或測試資料。"""
    rows, total = _run(lambda db: repository.list_suppliers(db, query, (page - 1) * 20))
    return [_display(row) for row in rows], total


def get_supplier(supplier_id: UUID) -> dict:
    """查無供應商回傳既有 404 錯誤契約。"""
    row = _run(lambda db: repository.get_supplier(db, supplier_id))
    if row is None:
        raise MissingError
    return _display(row)


def save(
    values: SupplierInput, actor: UUID, supplier_id: UUID | None = None, revision: str = ""
) -> UUID:
    """新增或更新；重複統編由資料庫唯一索引最終保證。"""

    def operation(db: Session) -> UUID:
        if supplier_id is None:
            return repository.create_supplier(db, values, actor)
        if not repository.update_supplier(db, supplier_id, values, revision_value(revision), actor):
            raise SupplierRuleError("資料已被其他人修改或已不存在，請重新載入後再操作")
        return supplier_id

    return _run(operation, write=True)


def set_status(supplier_id: UUID, active: bool, revision: str, actor: UUID) -> None:
    """停用保留資料；狀態更新與版本檢查同一交易完成。"""

    def operation(db: Session) -> None:
        if not repository.set_status(db, supplier_id, active, revision_value(revision), actor):
            raise SupplierRuleError("資料已被其他人修改或已不存在，請重新載入後再操作")

    _run(operation, write=True)


def _run[T](operation: Callable[[Session], T], *, write: bool = False) -> T:
    if not configuration_active():
        raise UnavailableError
    db = repository.open_session()
    try:
        repository.prepare(db)
        authorized = authorize_transaction(db)
        if authorized is not True:
            raise UnavailableError
        result = operation(db)
        if write:
            finish_transaction(db, authorized)
            db.commit()
        else:
            db.rollback()
        return result
    except Exception as error:
        db.rollback()
        if isinstance(error, (EError, SupplierRuleError)):
            raise
        if isinstance(error, repository.SQLAlchemyError):
            cause = getattr(error, "orig", None)
            if (
                getattr(cause, "sqlstate", None) == "23505"
                and getattr(getattr(cause, "diag", None), "constraint_name", "")
                == "ux_suppliers_country_tax"
            ):
                raise SupplierRuleError("同一國家／地區已有相同統編，包含已停用的供應商") from None
        raise SupplierUnavailableError from None
    finally:
        db.close()
