"""測試用的外部連線封鎖（根目錄與整合測試共用）。

預設全部拒絕：網路、SQLAlchemy 連線、直接 psycopg 連線。整合測試可傳入 allowed_db，
讓「唯一一個」已驗證的隔離 PostgreSQL（主機＋連接埠）通過；其他目標一律拒絕。
Windows asyncio 以 socketpair 的 loopback 連線建立程序內喚醒通道，仍然放行。
"""

import socket
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

import pytest

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
AllowedDb = Callable[[], tuple[str, int] | None]


def canonical_target(host: Any, port: Any) -> tuple[str, int] | None:
    """回送位址一律正規化；非回送主機或缺埠回傳 None。"""
    try:
        number = int(port)
    except (TypeError, ValueError):
        return None
    name = str(host or "")
    if name not in LOOPBACK:
        return None
    return ("::1" if name == "::1" else "127.0.0.1", number)


def install_network_block(
    monkeypatch: pytest.MonkeyPatch, allowed_db: AllowedDb = lambda: None
) -> None:
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

    def is_allowed(host: Any, port: Any) -> bool:
        allowed = allowed_db()
        return allowed is not None and canonical_target(host, port) == allowed

    def guarded_connect(connection: socket.socket, address: Any) -> Any:
        if inside_socketpair.get() and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(connection, address)
        if isinstance(address, tuple) and is_allowed(address[0], address[1]):
            return original_connect(connection, address)
        return deny_connection()

    def guarded_socketpair(*args: Any, **kwargs: Any) -> tuple[socket.socket, socket.socket]:
        token = inside_socketpair.set(True)
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            inside_socketpair.reset(token)

    def guarded_engine_connect(self: Engine, *args: Any, **kwargs: Any) -> Any:
        if not is_allowed(self.url.host, self.url.port):
            return deny_connection()
        return original_engine_connect(self, *args, **kwargs)

    def guarded_engine_raw(self: Engine, *args: Any, **kwargs: Any) -> Any:
        if not is_allowed(self.url.host, self.url.port):
            return deny_connection()
        return original_engine_raw(self, *args, **kwargs)

    def conninfo_allowed(args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
        try:
            info = conninfo_to_dict(args[0] if args else "", **kwargs)
        except Exception:
            return False
        if info.get("hostaddr") or info.get("service"):
            return False  # 不讓 hostaddr／service 繞過主機判斷
        return is_allowed(info.get("host"), info.get("port"))

    def guarded_psycopg_connect(*args: Any, **kwargs: Any) -> Any:
        if not conninfo_allowed(args, kwargs):
            return deny_connection()
        return original_psycopg_connect(*args, **kwargs)

    def guarded_connection_connect(*args: Any, **kwargs: Any) -> Any:
        if not conninfo_allowed(args, kwargs):
            return deny_connection()
        return original_connection_connect(*args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", deny_connection)
    monkeypatch.setattr(socket, "getaddrinfo", deny_connection)
    monkeypatch.setattr(socket, "socketpair", guarded_socketpair)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_connection)
    monkeypatch.setattr(socket.socket, "sendto", deny_connection)
    monkeypatch.setattr(Engine, "connect", guarded_engine_connect)
    monkeypatch.setattr(Engine, "raw_connection", guarded_engine_raw)
    monkeypatch.setattr(psycopg, "connect", guarded_psycopg_connect)
    monkeypatch.setattr(psycopg.Connection, "connect", guarded_connection_connect)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", deny_connection)
