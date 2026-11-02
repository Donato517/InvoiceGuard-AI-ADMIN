"""測試用的 Python 層連線封鎖（根目錄、整合測試與播種子程序共用）。

預設全部拒絕：Python socket、SQLAlchemy 連線、直接 psycopg 連線。
整合測試可傳入 allowed_db，只放行「已驗證的那一個」資料庫身分：(host, port, dbname, user)
四項必須完全相同；沒有明確指定 dbname 或 user、host 不是字面的 127.0.0.1／::1（含 localhost、Unix socket、
多主機）、帶 hostaddr／service，一律拒絕。

範圍與限制（這不是正式環境的安全機制，也不是 OS／容器層的網路隔離）：
  - 只攔截這個 Python 程序內的 socket、SQLAlchemy Engine 與 psycopg 進入點；
    其他原生函式庫、額外啟動的外部程序、SSH Tunnel／Port Forwarding 都不在保護範圍內。
  - PostgreSQL 連線走 libpq，不經過 Python socket，所以 Python socket 一律拒絕、不為資料庫放行；
    dbname／user 只能在 SQLAlchemy 與 psycopg 進入點比對。
Windows asyncio 以 socketpair 的 loopback 連線建立程序內喚醒通道，仍然放行。

此模組不得在匯入時依賴 pytest：播種子程序（API 的 venv）也會使用。
"""

import socket
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Protocol

# 只接受字面 IP：localhost 可能解析成 ::1 或 127.0.0.1，兩者可能是不同的服務。
LOOPBACK_IPS = frozenset({"127.0.0.1", "::1"})
DB_IDENTITY_DENIED_KEYS = ("hostaddr", "service")

DbIdentity = tuple[str, int, str, str]  # host, port, dbname, user
AllowedDb = Callable[[], DbIdentity | None]


class Patcher(Protocol):
    """pytest 的 MonkeyPatch 與播種子程序的 ProcessPatcher 都符合此介面。"""

    def setattr(self, target: Any, name: str, value: Any) -> None: ...


class ProcessPatcher:
    """播種子程序用：永久替換，不還原（程序結束即消失）。"""

    def setattr(self, target: Any, name: str, value: Any) -> None:
        setattr(target, name, value)


def db_identity(host: Any, port: Any, dbname: Any, user: Any) -> DbIdentity | None:
    """host 必須是字面回送 IP；port、dbname、user 缺任何一項回傳 None。"""
    try:
        number = int(port)
    except (TypeError, ValueError):
        return None
    if host not in LOOPBACK_IPS or not dbname or not user:
        return None
    return (str(host), number, str(dbname), str(user))


def install_network_block(patcher: Patcher, allowed_db: AllowedDb = lambda: None) -> None:
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from sqlalchemy.engine import Engine

    original_connect = socket.socket.connect
    original_socketpair = socket.socketpair
    original_engine_connect = Engine.connect
    original_engine_raw = Engine.raw_connection
    original_psycopg_connect = psycopg.connect
    original_connection_connect = psycopg.Connection.connect
    inside_socketpair: ContextVar[bool] = ContextVar("inside_socketpair", default=False)

    def deny_connection(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("單元測試禁止外部網路或真實資料庫連線")

    def is_allowed(host: Any, port: Any, dbname: Any, user: Any) -> bool:
        allowed = allowed_db()
        return allowed is not None and db_identity(host, port, dbname, user) == allowed

    def guarded_connect(connection: socket.socket, address: Any) -> Any:
        # 只放行 socketpair 的程序內喚醒通道；資料庫走 libpq，不需要 Python socket。
        if inside_socketpair.get() and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(connection, address)
        return deny_connection()

    def guarded_socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
        token = inside_socketpair.set(True)
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            inside_socketpair.reset(token)

    def guarded_engine_connect(self: Engine, *args: Any, **kwargs: Any) -> Any:
        url = self.url
        if url.query or not is_allowed(url.host, url.port, url.database, url.username):
            return deny_connection()
        return original_engine_connect(self, *args, **kwargs)

    def guarded_engine_raw(self: Engine, *args: Any, **kwargs: Any) -> Any:
        url = self.url
        if url.query or not is_allowed(url.host, url.port, url.database, url.username):
            return deny_connection()
        return original_engine_raw(self, *args, **kwargs)

    def conninfo_allowed(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
        try:
            info = conninfo_to_dict(args[0] if args else "", **kwargs)
        except Exception:
            return False
        if any(info.get(key) for key in DB_IDENTITY_DENIED_KEYS):
            return False  # hostaddr／service 可繞過主機判斷
        return is_allowed(info.get("host"), info.get("port"), info.get("dbname"), info.get("user"))

    def guarded_psycopg_connect(*args: Any, **kwargs: Any) -> Any:
        if not conninfo_allowed(args, kwargs):
            return deny_connection()
        return original_psycopg_connect(*args, **kwargs)

    def guarded_connection_connect(*args: Any, **kwargs: Any) -> Any:
        if not conninfo_allowed(args, kwargs):
            return deny_connection()
        return original_connection_connect(*args, **kwargs)

    patcher.setattr(socket, "create_connection", deny_connection)
    patcher.setattr(socket, "getaddrinfo", deny_connection)
    patcher.setattr(socket, "socketpair", guarded_socketpair)
    patcher.setattr(socket.socket, "connect", guarded_connect)
    patcher.setattr(socket.socket, "connect_ex", deny_connection)
    patcher.setattr(socket.socket, "sendto", deny_connection)
    patcher.setattr(Engine, "connect", guarded_engine_connect)
    patcher.setattr(Engine, "raw_connection", guarded_engine_raw)
    patcher.setattr(psycopg, "connect", guarded_psycopg_connect)
    patcher.setattr(psycopg.Connection, "connect", guarded_connection_connect)
    patcher.setattr(psycopg.AsyncConnection, "connect", deny_connection)
