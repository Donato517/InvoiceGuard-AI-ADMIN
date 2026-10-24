"""
模組名稱：app.db.repositories.db_health_repository
功能說明：暫時的 DB 連線診斷——以既有 Engine／Session 在唯讀交易執行 SELECT 1。
注意事項：
    - 不讀任何資料表、不接受外部 SQL；不 commit，Session 由 service 關閉（rollback）。
    - 錯誤只轉成固定代碼；驅動訊息含主機與帳號名稱，不回傳、不記錄原文。
    - 連線驗證完成後與 GET /health/db 一併移除。
"""

from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_engine

HealthCode = Literal[
    "configuration_unavailable",
    "auth_failed",
    "access_denied",
    "database_not_found",
    "host_not_found",
    "network_unreachable",
    "connect_timeout",
    "tls_failed",
    "too_many_connections",
    "db_unavailable",
    "query_timeout",
    "query_failed",
]


class HealthCheckError(RuntimeError):
    """連線診斷的固定代碼；不含任何設定值或驅動原文。"""

    def __init__(self, code: HealthCode) -> None:
        self.code = code
        super().__init__(code)


def open_session() -> Session:
    """沿用既有 Engine 與 Session 工廠；DATABASE_URL 空白或格式不符時回報設定錯誤。"""
    try:
        return SessionLocal(bind=get_engine())
    except (SQLAlchemyError, ValueError):
        raise HealthCheckError("configuration_unavailable") from None


def ping(session: Session) -> int:
    """建立連線後在唯讀交易執行 SELECT 1 並回傳結果。"""
    try:
        session.connection()
    except SQLAlchemyError as error:
        raise HealthCheckError(_connect_failure(error)) from None
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '4000ms'"))
        result = session.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError as error:
        state = getattr(getattr(error, "orig", None), "sqlstate", None)
        raise HealthCheckError(
            "query_timeout" if state in {"57014", "55P03"} else "query_failed"
        ) from None
    if result != 1:
        raise HealthCheckError("query_failed")
    return result


def _connect_failure(error: SQLAlchemyError) -> HealthCode:
    # 只在此比對驅動訊息的固定片段，絕不回傳、記錄或保存原文
    message = str(getattr(error, "orig", None) or "").lower()
    if 'database "' in message and "does not exist" in message:
        return "database_not_found"
    if any(
        part in message
        for part in ("password authentication failed", "tenant or user not found", "does not exist")
    ):
        return "auth_failed"
    if "no pg_hba.conf entry" in message:
        return "access_denied"
    if any(
        part in message
        for part in ("could not translate host name", "name or service not known", "no such host")
    ):
        return "host_not_found"
    if any(part in message for part in ("network is unreachable", "cannot assign requested")):
        return "network_unreachable"
    if "timeout expired" in message or "timed out" in message:
        return "connect_timeout"
    if "ssl" in message:
        return "tls_failed"
    if any(
        part in message
        for part in ("too many connections", "remaining connection slots", "max client connections")
    ):
        return "too_many_connections"
    return "db_unavailable"
