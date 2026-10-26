"""
模組名稱：app.services.db_inspection_service
功能說明：控制管理者唯讀 DB 盤點及 SELECT 1 連線檢查的獨立交易與清理，不借用授權查詢的 Session。
注意事項：所有查詢由 repository 執行；成功與失敗都 rollback，絕不 commit。
"""

from contextlib import suppress

from app.db.repositories import db_inspection_repository as repository
from app.db.repositories.db_inspection_repository import HealthCheckError, InspectionError
from app.schemas.db_inspection import DatabaseInspection


def check_connection() -> int:
    """以 DB 模組的 Engine／Session 執行唯讀 SELECT 1 並回傳結果；失敗只拋出固定代碼。"""
    try:
        session = repository.open_session()
        try:
            return repository.ping(session)
        finally:
            # close 會 rollback 並歸還連線；清理失敗不可蓋掉原本的檢查結果。
            with suppress(Exception):
                session.close()
    except HealthCheckError:
        raise
    except Exception:
        raise HealthCheckError("query_failed") from None


def inspect_table(target: str) -> DatabaseInspection:
    """盤點一個核定資料表；使用合法 DB 連線設定，所有錯誤均以固定代碼拒絕。"""
    repository.validate_target(target)
    try:
        connection = repository.open_connection()
        try:
            transaction = connection.begin()
            try:
                repository.prepare_read_only(connection)
                return repository.inspect_table(connection, target)
            finally:
                transaction.rollback()
        finally:
            connection.close()
    except InspectionError:
        raise
    except Exception:
        # 包含 begin／rollback／close 的非預期失敗；不可將連線字串帶進 HTTP 或日誌。
        raise InspectionError("query_failed") from None
