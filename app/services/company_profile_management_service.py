"""本公司基本資料的查看與修改交易；沿用部門四操作授權，並於同一交易重新驗證。"""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.repositories import company_profile_repository as repository
from app.schemas.company_profile import CompanyProfileInput
from app.schemas.e_admin import EError, UnavailableError
from app.services.e_configuration_security import (
    authorize_transaction,
    configuration_active,
    finish_transaction,
)
from app.services.format21_service import is_valid_ban


class CompanyProfileRuleError(Exception):
    """公司資料表單可呈現的固定業務錯誤。"""


class CompanyProfileUnavailableError(UnavailableError):
    """不公開底層連線或 SQL 的錯誤。"""

    message = "公司基本資料暫時無法讀寫，請確認資料庫結構與服務權限已完成設定後重試"


def _display(row: dict) -> dict:
    for name in ("created_at", "updated_at"):
        row[name] = row[name].astimezone(ZoneInfo("Asia/Taipei")).isoformat()
    return row


def _revision(value: str) -> datetime:
    """拒絕缺少時區的版本，避免伺服器時區造成誤判。"""
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is not None:
            return result
    except ValueError:
        pass
    raise CompanyProfileRuleError("資料版本無效，請重新開啟畫面")


def get_profile() -> dict | None:
    """讀取公司基本資料；尚未設定時為 None。"""
    row = _run(repository.get_profile)
    return _display(row) if row else None


def save(values: CompanyProfileInput, actor: UUID, revision: str = "") -> None:
    """第一次設定或修改；統編須通過檢查碼。修改以資料版本比對，避免覆蓋他人修改。

    Raises:
        CompanyProfileRuleError: 統編檢查碼錯誤、他人已先設定或資料已被修改。
    """
    if not is_valid_ban(values.ban):
        raise CompanyProfileRuleError("統一編號未通過檢查碼驗算，請再確認")

    def operation(db: Session) -> None:
        if not revision:
            if not repository.create_profile(db, values, actor):
                raise CompanyProfileRuleError("公司資料已由其他人設定，請重新載入後再修改")
        elif not repository.update_profile(db, values, _revision(revision), actor):
            raise CompanyProfileRuleError("資料已被其他人修改，請重新載入後再操作")

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
        if isinstance(error, (EError, CompanyProfileRuleError)):
            raise
        raise CompanyProfileUnavailableError from None
    finally:
        db.close()
