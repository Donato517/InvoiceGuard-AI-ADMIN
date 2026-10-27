"""
模組名稱：app.services.dev_account_service
功能說明：本機開發者帳號頁的查詢、新增、修改——以既有 DB 模組直接存取雲端 PostgreSQL。
相依模組：app.services.user_service、app.db.repositories.user_account_repository
注意事項：
    - DATABASE_URL 由 START.cmd 經 Vercel CLI 取得後只注入本機 ADMIN 程序；本模組不讀環境變數。
    - 讀寫與雲端帳號 API 相同，由 user_service → user_account_repository 執行，交易由 user_service 控制。
    - 每次操作開一個 Session，結束一律 close；畫面訊息一律使用本模組的固定文字。
"""

import logging
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.db.repositories import user_account_repository
from app.schemas.account import AccountSummary
from app.services import user_service

logger = logging.getLogger(__name__)

MESSAGES = {
    "database_not_configured": "本機 ADMIN 沒有 DATABASE_URL；請設定 VERCEL_TOKEN 後以 START.cmd 啟動",
    "db_unavailable": "雲端 DB 連線或查詢失敗",
    "invalid_email": "Email 格式不正確",
    "password_required": "請輸入密碼",
    "email_taken": "此 Email 已被使用",
    "current_password_invalid": "目前密碼不正確",
    "not_found": "找不到此帳號",
    "account_table_missing": "雲端 DB 缺少帳號相關資料表",
    "account_schema_mismatch": "雲端帳號資料表欄位與程式契約不符",
    "account_permission_denied": "雲端 DB 系統帳號沒有帳號資料表的存取權限",
    "default_department_unavailable": "雲端預設部門（UNASSIGNED）已停用",
}


class DevAccountError(RuntimeError):
    """查詢／新增／修改失敗；message 為固定畫面文字。"""

    def __init__(self, code: str) -> None:
        self.code = code if code in MESSAGES else "db_unavailable"
        super().__init__(MESSAGES[self.code])


def search(query: str = "") -> list[AccountSummary]:
    """查詢帳號；query 空白列出最近帳號，可為 UUID 或 Email。"""
    return _run(lambda db: user_service.search_accounts(db, query))


def create(*, email: str, password: str) -> AccountSummary:
    """新增帳號並回傳寫入後的結果。"""
    return _run(lambda db: user_service.create_account(db, email=email, password=password))


def update(
    user_id: UUID, *, email: str, current_password: str, new_password: str
) -> AccountSummary:
    """以目前密碼修改自己的帳號；new_password 空白表示不變更。"""
    return _run(
        lambda db: user_service.update_account(
            db,
            user_id,
            email=email,
            current_password=current_password,
            new_password=new_password or None,
        )
    )


def _run[T](operation: Callable[[Session], T]) -> T:
    try:
        db = user_account_repository.open_session()
    except user_service.AccountStoreError as error:
        raise DevAccountError(error.code) from None
    try:
        return operation(db)
    except (user_service.AccountInputError, user_service.AccountStoreError) as error:
        raise DevAccountError(error.code) from None
    except NotFoundError:
        raise DevAccountError("not_found") from None
    except Exception as error:
        # 連線／查詢失敗：驅動訊息含主機與帳號名稱，日誌只記例外類別
        logger.warning("dev_account_db_error type=%s", type(error).__name__)
        raise DevAccountError("db_unavailable") from None
    finally:
        db.close()
