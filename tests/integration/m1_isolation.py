"""M1 整合測試的隔離資料庫驗證與子程序環境（測試專用，不屬於應用程式）。

ISOLATED_CASE_DB_URL 必須同時滿足，否則 fail-closed（不執行任何測試寫入）：
  1. 驅動為 postgresql+psycopg、主機為字面 127.0.0.1／::1（不接受 localhost）、明確連接埠、使用者必填、
     資料庫名稱以 ig_test 開頭、無任何查詢參數（不允許 host／hostaddr／service／options 改變實際目標）。
  2. 連線後，伺服器上的資料庫層設定 invoiceguard.m1_test_marker 必須等於環境變數
     ISOLATED_CASE_DB_MARKER（至少 16 字元）。標記只在建立受控測試庫時以
     ALTER DATABASE … SET invoiceguard.m1_test_marker = '<token>' 寫入。
驗證通過的身分是 (host, port, dbname, user) 四項；之後的連線封鎖（network_guard）只放行這四項完全相同的連線。

限制：標記只在「連線驗證當下」證明該資料庫是有人刻意標記的測試庫（測試程序與播種子程序各驗證一次），
不能證明沒有 SSH Tunnel／Port Forwarding 把別的目標轉到本機並複製標記；名稱前綴只是輔助。
這是測試用的 Python 層防護，不是正式環境的安全機制，也不是 OS／容器層的網路隔離。
"""

import hmac
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import network_guard
import psycopg
from sqlalchemy.engine import URL, make_url

URL_ENV = "ISOLATED_CASE_DB_URL"
MARKER_ENV = "ISOLATED_CASE_DB_MARKER"
MARKER_SETTING = "invoiceguard.m1_test_marker"
DB_NAME_PREFIX = "ig_test"
MIN_MARKER_LENGTH = 16
SEED_URL_ENV = "M1_SEED_DB_URL"
IS_WINDOWS = os.name == "nt"

# 在任何 monkeypatch 之前保存真實連線函式，只用於驗證標記。
_REAL_CONNECT = psycopg.connect

# 通過驗證的唯一資料庫身分（host, port, dbname, user）；連線封鎖只放行與它完全相同的連線。
ACTIVE_TARGET: network_guard.DbIdentity | None = None


class UnsafeTargetError(Exception):
    """隔離資料庫設定不安全或無法證明受控。"""


def validate_url(raw: str) -> URL:
    """只做靜態檢查，不連線。"""
    try:
        url = make_url(raw)
    except Exception:
        raise UnsafeTargetError(f"{URL_ENV} 不是有效的資料庫 URL") from None
    if url.drivername != "postgresql+psycopg":
        raise UnsafeTargetError(f"{URL_ENV} 驅動必須是 postgresql+psycopg")
    if url.host not in network_guard.LOOPBACK_IPS or url.port is None:
        raise UnsafeTargetError(
            f"{URL_ENV} 必須是字面 127.0.0.1／::1（不接受 localhost）並明確指定連接埠"
        )
    if not url.username or not (url.database or "").startswith(DB_NAME_PREFIX):
        raise UnsafeTargetError(f"{URL_ENV} 的使用者必填，且資料庫名稱須以 {DB_NAME_PREFIX} 開頭")
    if url.query:
        raise UnsafeTargetError(f"{URL_ENV} 不允許查詢參數（可改變實際連線目標）")
    return url


def identity_of(url: URL) -> network_guard.DbIdentity:
    """已通過 validate_url 的 URL 的四項連線身分。"""
    identity = network_guard.db_identity(url.host, url.port, url.database, url.username)
    if identity is None:
        raise UnsafeTargetError("資料庫身分不完整")
    return identity


def install_process_guard(url: URL) -> None:
    """播種子程序用：永久封鎖 Python 層連線，只放行與 url 完全相同的資料庫身分。"""
    identity = identity_of(url)
    network_guard.install_network_block(network_guard.ProcessPatcher(), lambda: identity)


def verify_marker(url: URL, marker: str, connect: Callable[..., Any] = _REAL_CONNECT) -> None:
    """連到該資料庫讀取標記；標記不符或讀不到一律拒絕。"""
    if len(marker) < MIN_MARKER_LENGTH:
        raise UnsafeTargetError(f"{MARKER_ENV} 未設定或少於 {MIN_MARKER_LENGTH} 字元")
    try:
        with connect(
            host=url.host,
            port=url.port,
            user=url.username,
            password=url.password,
            dbname=url.database,
            connect_timeout=5,
        ) as conn:
            row = conn.execute(
                "SELECT current_database(), current_setting(%s, true)", (MARKER_SETTING,)
            ).fetchone()
    except UnsafeTargetError:
        raise
    except Exception:
        raise UnsafeTargetError("無法連線到隔離資料庫以驗證標記") from None
    found = (row[1] or "") if row else ""
    if not row or row[0] != url.database or not hmac.compare_digest(found, marker):
        raise UnsafeTargetError("資料庫缺少或不符合受控測試庫標記，拒絕執行")


def load_target(
    environ: Mapping[str, str], connect: Callable[..., Any] = _REAL_CONNECT
) -> URL | None:
    """未設定 URL → None（呼叫端略過）；已設定但不安全 → UnsafeTargetError。"""
    raw = environ.get(URL_ENV, "")
    if not raw:
        return None
    url = validate_url(raw)
    verify_marker(url, environ.get(MARKER_ENV, ""), connect)
    return url


def seed_environment(api_root: Path, url: URL, marker: str) -> dict[str, str]:
    """播種子程序的環境：完全白名單，不繼承 DATABASE_URL、Storage、Token、API Key。

    資料庫 URL 走環境變數而非命令列參數，避免出現在程序列表；標記（非機密的隨機字串）讓子程序
    在第一次寫入前自行再驗證一次。
    """
    env = {
        "PYTHONPATH": str(api_root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": os.environ.get("PATH", ""),
        SEED_URL_ENV: url.render_as_string(hide_password=False),
        MARKER_ENV: marker,
    }
    if IS_WINDOWS:
        # Windows 的自訂 env 若沒有 SYSTEMROOT，子程序的 Winsock 無法初始化（WinError 10106），
        # psycopg 會匯入失敗。它是系統路徑、不是機密；缺少時明確失敗，不靜默放寬白名單。
        system_root = os.environ.get("SYSTEMROOT", "")
        if not system_root:
            raise UnsafeTargetError("Windows 缺少 SYSTEMROOT，無法建立播種子程序環境")
        env["SYSTEMROOT"] = system_root
    return env
