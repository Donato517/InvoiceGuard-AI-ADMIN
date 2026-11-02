"""M1 整合測試的隔離資料庫驗證與子程序環境（測試專用，不屬於應用程式）。

ISOLATED_CASE_DB_URL 必須同時滿足，否則 fail-closed（不執行任何測試寫入）：
  1. 驅動為 postgresql+psycopg、主機為回送位址、明確連接埠、資料庫名稱以 ig_test 開頭、無任何查詢參數
     （不允許 host／hostaddr／service／options 等參數改變實際目標）。
  2. 連線後，伺服器上的資料庫層設定 invoiceguard.m1_test_marker 必須等於環境變數
     ISOLATED_CASE_DB_MARKER（至少 16 字元）。標記只在建立受控測試庫時以
     ALTER DATABASE … SET invoiceguard.m1_test_marker = '<token>' 寫入。

限制：這證明「該資料庫是有人刻意標記的測試庫」，不能證明沒有 SSH Tunnel／Port Forwarding 把標記庫
以外的目標轉到本機；名稱前綴只是輔助。
"""

import hmac
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy.engine import URL, make_url

from tests.network_guard import LOOPBACK, canonical_target

URL_ENV = "ISOLATED_CASE_DB_URL"
MARKER_ENV = "ISOLATED_CASE_DB_MARKER"
MARKER_SETTING = "invoiceguard.m1_test_marker"
DB_NAME_PREFIX = "ig_test"
MIN_MARKER_LENGTH = 16
SEED_URL_ENV = "M1_SEED_DB_URL"

# 在任何 monkeypatch 之前保存真實連線函式，只用於驗證標記。
_REAL_CONNECT = psycopg.connect

# 通過驗證的唯一資料庫目標（主機＋連接埠）；整合測試的連線封鎖只放行它。
ACTIVE_TARGET: tuple[str, int] | None = None


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
    if url.host not in LOOPBACK or canonical_target(url.host, url.port) is None:
        raise UnsafeTargetError(f"{URL_ENV} 必須指向回送位址並明確指定連接埠")
    if not url.username or not (url.database or "").startswith(DB_NAME_PREFIX):
        raise UnsafeTargetError(f"{URL_ENV} 的使用者必填，且資料庫名稱須以 {DB_NAME_PREFIX} 開頭")
    if url.query:
        raise UnsafeTargetError(f"{URL_ENV} 不允許查詢參數（可改變實際連線目標）")
    return url


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


def seed_environment(api_root: Path, url: URL) -> dict[str, str]:
    """播種子程序的環境：完全白名單，不繼承 DATABASE_URL、Storage、Token、API Key。

    資料庫 URL 走環境變數而非命令列參數，避免出現在程序列表。
    """
    return {
        "PYTHONPATH": str(api_root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": os.environ.get("PATH", ""),
        SEED_URL_ENV: url.render_as_string(hide_password=False),
    }
