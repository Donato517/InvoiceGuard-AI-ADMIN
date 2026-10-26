"""
模組名稱：app.services.db_health_service
功能說明：暫時的 DB 連線檢查——回傳並記錄 APP_ENV、DATABASE_URL 是否存在、SELECT 1 結果。
相依模組：app.db.repositories.db_health_repository
注意事項：
    - 回應與日誌只含固定欄位、代碼與 true／false，不含連線字串、主機、帳號或密碼。
    - 本機 ADMIN（MODULE_BACKEND=remote）以 GET /health/db 取得同一結果，本機不持有 DATABASE_URL。
    - 成功結果每個執行個體只連一次，避免公開端點被反覆觸發連線；失敗不快取，可再次檢查。
    - 連線驗證完成後與 GET /health/db 一併移除。
"""

import logging
from contextlib import suppress
from functools import lru_cache

from app.core.config import settings
from app.db.repositories import db_health_repository as repository
from app.db.repositories.db_health_repository import HealthCheckError

logger = logging.getLogger(__name__)


def connection_status() -> dict[str, object]:
    """執行唯讀 SELECT 1，回傳並寫入一行 DB_DIAGNOSTIC 日誌。

    Returns:
        app_env、database_url_present、select_1（成功）或 code（失敗）、status（DB_OK／DB_ERROR）。
    """
    body: dict[str, object] = {
        "app_env": settings.APP_ENV,
        "database_url_present": bool(settings.DATABASE_URL.strip()),
    }
    prefix = "DB_DIAGNOSTIC app_env=%s database_url_present=%s"
    try:
        body["select_1"] = _confirmed_select_one()
    except HealthCheckError as error:
        logger.warning(prefix + " status=DB_ERROR code=%s", *body.values(), error.code)
        body |= {"status": "DB_ERROR", "code": error.code}
        return body
    logger.info(prefix + " select_1=%s status=DB_OK", *body.values())
    body["status"] = "DB_OK"
    return body


def check_connection() -> int:
    """以既有 Engine／Session 執行唯讀 SELECT 1 並回傳結果；失敗只拋出固定代碼。"""
    try:
        session = repository.open_session()
        try:
            return repository.ping(session)
        finally:
            # close 會 rollback 並歸還連線；清理失敗不可蓋掉原本的診斷結果
            with suppress(Exception):
                session.close()
    except HealthCheckError:
        raise
    except Exception:
        raise HealthCheckError("query_failed") from None


@lru_cache(maxsize=1)
def _confirmed_select_one() -> int:
    return check_connection()
