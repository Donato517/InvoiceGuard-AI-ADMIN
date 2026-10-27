"""
模組名稱：app.db.repositories.db_inspection_repository
功能說明：固定資料表的唯讀連線、catalog 查詢及精確 COUNT，以及不讀任何資料表的 SELECT 1
    連線檢查；不讀取帳密或個資內容。
注意事項：交易由 service 控制；本模組不 begin、commit 或 rollback，不接受任意 SQL。
"""

from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.module_errors import ModuleConfigurationError
from app.db.session import SessionLocal, get_engine
from app.schemas.db_inspection import DatabaseInspection, InspectedTable, InspectionColumn

# 每條 COUNT 都是程式核定的完整常數；請求值從未用於 SQL 字串拼接。
COUNT_QUERIES = {
    "public.user_profiles": 'SELECT count(*) FROM ONLY "public"."user_profiles"',
    "public.departments": 'SELECT count(*) FROM ONLY "public"."departments"',
    "public.user_departments": 'SELECT count(*) FROM ONLY "public"."user_departments"',
    "public.users": 'SELECT count(*) FROM ONLY "public"."users"',
    "invoiceguard_identity.user_accounts": (
        'SELECT count(*) FROM ONLY "invoiceguard_identity"."user_accounts"'
    ),
}

RELATION_QUERY = text("""
    SELECT c.oid AS relation_id, c.relkind,
           pg_catalog.has_schema_privilege(n.oid, 'USAGE') AS schema_usage,
           CASE WHEN c.relkind IN ('r', 'p')
                THEN pg_catalog.has_table_privilege(c.oid, 'SELECT')
                ELSE false END AS can_select,
           CASE WHEN c.relkind IN ('r', 'p')
                THEN pg_catalog.row_security_active(c.oid)
                ELSE false END AS rls_active,
           EXISTS (SELECT 1 FROM pg_catalog.pg_inherits AS i
                   WHERE i.inhparent = c.oid) AS has_children
    FROM pg_catalog.pg_class AS c
    JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relname = :table
""")
COLS_QUERY = text("""
    SELECT a.attname AS name,
           pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
           NOT a.attnotnull AS nullable
    FROM pg_catalog.pg_attribute AS a
    WHERE a.attrelid = :relation_id AND a.attnum > 0 AND NOT a.attisdropped
    ORDER BY a.attnum
""")

InspectionCode = Literal[
    "target_not_allowed",
    "configuration_unavailable",
    "db_unavailable",
    "query_timeout",
    "query_failed",
]


class InspectionError(RuntimeError):
    """僅攜帶固定錯誤代碼；原始 DB 例外不得回傳或記錄。"""

    def __init__(self, code: InspectionCode) -> None:
        self.code = code
        super().__init__(code)


def validate_target(target: str) -> tuple[str, str]:
    """只接受核定的完整表名，未核定值不建立連線也不回顯。"""
    if target not in COUNT_QUERIES:
        raise InspectionError("target_not_allowed")
    schema, name = target.split(".", 1)
    return schema, name


def open_connection() -> Connection:
    """沿用既有 DB 設定驗證，建立獨立盤點連線。"""
    try:
        return get_engine().connect()
    except ModuleConfigurationError:
        raise InspectionError("configuration_unavailable") from None
    except (SQLAlchemyError, OSError, ValueError):
        raise InspectionError("db_unavailable") from None


def prepare_read_only(connection: Connection) -> None:
    """設定唯讀快照、有限等待與 RLS 過濾拒絕；所有設定只在本交易有效。"""
    try:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '4000ms'"))
        connection.execute(text("SET LOCAL lock_timeout = '1000ms'"))
        # off 不會繞過 RLS；受 RLS 過濾的查詢會報錯，避免並行政策變動導致假零筆。
        connection.execute(text("SET LOCAL row_security = off"))
    except SQLAlchemyError as error:
        raise _safe_query_error(error) from None


def inspect_table(connection: Connection, target: str) -> DatabaseInspection:
    """查詢固定表的結構與筆數；權限不足或 RLS 生效時明確不計數。"""
    schema, name = validate_target(target)
    try:
        if connection.execute(text("SELECT 1")).scalar_one() != 1:
            raise InspectionError("query_failed")
        relation = (
            connection.execute(RELATION_QUERY, {"schema": schema, "table": name})
            .mappings()
            .one_or_none()
        )
        if relation is None:
            return DatabaseInspection(
                table=InspectedTable(
                    schema_name=schema, name=name, exists=False, count_status="missing"
                )
            )
        columns = [
            InspectionColumn(**dict(row))
            for row in connection.execute(
                COLS_QUERY, {"relation_id": relation["relation_id"]}
            ).mappings()
        ]
        result = InspectedTable(
            schema_name=schema,
            name=name,
            exists=True,
            columns=columns,
            count_status="unsupported_relation",
        )
        if relation["relkind"] != "r" or relation["has_children"]:
            return DatabaseInspection(table=result)
        if not relation["schema_usage"] or not relation["can_select"]:
            result.count_status = "permission_denied"
        elif relation["rls_active"]:
            result.count_status = "rls_active"
        else:
            result.row_count = int(connection.execute(text(COUNT_QUERIES[target])).scalar_one())
            result.count_status = "exact"
        return DatabaseInspection(table=result)
    except SQLAlchemyError as error:
        raise _safe_query_error(error) from None


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
    """連線檢查的固定代碼；issues 只含設定欄位與代碼，不含任何設定值。"""

    def __init__(self, code: HealthCode, issues: tuple[str, ...] = ()) -> None:
        self.code = code
        self.issues = issues
        super().__init__(code)


def open_session() -> Session:
    """沿用 DB 模組的 Engine 與 Session 工廠；設定不合格時只回報欄位與代碼。"""
    try:
        return SessionLocal(bind=get_engine())
    except ModuleConfigurationError as error:
        issues = tuple(
            f"{issue.field}:{issue.code}"
            for issue in error.report.issues
            if issue.severity == "error"
        )
        raise HealthCheckError("configuration_unavailable", issues) from None
    except (SQLAlchemyError, ValueError):
        raise HealthCheckError("configuration_unavailable") from None


def ping(session: Session) -> int:
    """建立連線後在唯讀交易執行 SELECT 1 並回傳結果；不讀取任何資料表、不接受外部 SQL。"""
    try:
        session.connection()
    except SQLAlchemyError as error:
        raise HealthCheckError(_connect_failure(error)) from None
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout = '4000ms'"))
        result = session.execute(text("SELECT 1")).scalar_one()
    except SQLAlchemyError as error:
        raise HealthCheckError(_safe_query_error(error).code) from None
    if result != 1:
        raise HealthCheckError("query_failed")
    return result


def _connect_failure(error: SQLAlchemyError) -> HealthCode:
    # 驅動訊息含主機與帳號名稱：只在此比對固定片段，絕不回傳、記錄或保存原文。
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


def _safe_query_error(error: SQLAlchemyError) -> InspectionError:
    # 僅使用 SQLSTATE 分類，從不轉字串、回傳 query、參數或原始例外。
    state = getattr(getattr(error, "orig", None), "sqlstate", None)
    return InspectionError("query_timeout" if state in {"57014", "55P03"} else "query_failed")
