"""整合測試隔離防護的負向測試：全部使用替身，不會真的連到任何外部服務或資料庫。

涵蓋：非本機 URL、未標記的本機目標、非 PostgreSQL 外連、正式 DATABASE_URL、未設定時略過、
合法目標仍被放行（以替身驗證，不連線）、Windows socketpair 行為保留。
"""

import socket
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import m1_isolation
import psycopg
import pytest
from sqlalchemy import create_engine

from tests.network_guard import install_network_block

MARKER = "m1-test-marker-0123456789"
GOOD_URL = "postgresql+psycopg://postgres:pw@127.0.0.1:55433/ig_test"
OTHER_PORT_URL = "postgresql+psycopg://postgres:pw@127.0.0.1:5432/ig_test"
PROD_LIKE_URL = "postgresql+psycopg://u:p@db.prod-example.supabase.co:5432/postgres"


def fake_connect(db: str = "ig_test", marker: str | None = MARKER) -> MagicMock:
    """模擬 psycopg.connect：回傳 (資料庫名, 標記)；不會連線。"""
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = (db, marker)
    return MagicMock(return_value=connection)


@pytest.fixture
def allowed_target(monkeypatch: pytest.MonkeyPatch) -> tuple[str, int]:
    target = ("127.0.0.1", 55433)
    monkeypatch.setattr(m1_isolation, "ACTIVE_TARGET", target)
    return target


# --- 一、URL 靜態檢查（非本機、驅動、名稱、查詢參數）---


@pytest.mark.parametrize(
    "raw",
    [
        PROD_LIKE_URL,
        "postgresql+psycopg://u:p@10.0.0.5:5432/ig_test",
        "postgresql+psycopg://u:p@127.0.0.1/ig_test",  # 缺連接埠
        "postgresql+psycopg://u:p@127.0.0.1:5432/postgres",  # 名稱前綴不符
        "postgresql://u:p@127.0.0.1:5432/ig_test",  # 驅動不符
        "postgresql+psycopg://u:p@127.0.0.1:5432/ig_test?host=db.prod-example.com",
        "postgresql+psycopg://u:p@127.0.0.1:5432/ig_test?hostaddr=203.0.113.9",
        "postgresql+psycopg://u:p@127.0.0.1:5432/ig_test?service=prod",
        "not a url",
    ],
)
def test_unsafe_urls_are_rejected_without_connecting(raw: str) -> None:
    connect = fake_connect()

    with pytest.raises(m1_isolation.UnsafeTargetError):
        m1_isolation.load_target(
            {m1_isolation.URL_ENV: raw, m1_isolation.MARKER_ENV: MARKER}, connect
        )

    connect.assert_not_called()


# --- 二、本機但未經認證的目標（標記缺漏／不符）---


@pytest.mark.parametrize(
    ("marker_env", "server_marker"),
    [
        ("", MARKER),  # 沒有設定環境標記
        ("short", "short"),  # 標記過短
        (MARKER, None),  # 資料庫沒有標記（例如本機的另一個庫或被轉發的庫）
        (MARKER, "another-marker-0123456789"),  # 標記不符
    ],
)
def test_local_target_without_matching_marker_is_rejected(
    marker_env: str, server_marker: str | None
) -> None:
    with pytest.raises(m1_isolation.UnsafeTargetError):
        m1_isolation.load_target(
            {m1_isolation.URL_ENV: GOOD_URL, m1_isolation.MARKER_ENV: marker_env},
            fake_connect(marker=server_marker),
        )


def test_marker_check_failure_to_connect_is_rejected() -> None:
    with pytest.raises(m1_isolation.UnsafeTargetError):
        m1_isolation.load_target(
            {m1_isolation.URL_ENV: GOOD_URL, m1_isolation.MARKER_ENV: MARKER},
            MagicMock(side_effect=OSError("refused")),
        )


def test_matching_marker_is_accepted() -> None:
    url = m1_isolation.load_target(
        {m1_isolation.URL_ENV: GOOD_URL, m1_isolation.MARKER_ENV: MARKER}, fake_connect()
    )

    assert url is not None
    assert (url.host, url.port, url.database) == ("127.0.0.1", 55433, "ig_test")


def test_unset_url_means_skip_not_failure() -> None:
    assert m1_isolation.load_target({}, fake_connect()) is None


# --- 三、非 PostgreSQL 的外部連線與其他資料庫目標被封鎖 ---


def test_network_is_blocked_when_no_database_is_authorised() -> None:
    assert m1_isolation.ACTIVE_TARGET is None  # 未驗證任何目標時，連本機資料庫也不放行

    with pytest.raises(AssertionError):
        socket.create_connection(("example.com", 80))
    with pytest.raises(AssertionError):
        socket.getaddrinfo("example.com", 80)
    with pytest.raises(AssertionError):
        socket.socket().connect(("203.0.113.10", 443))
    with pytest.raises(AssertionError):
        psycopg.connect(host="127.0.0.1", port=55433, user="x", dbname="ig_test")
    with pytest.raises(AssertionError):
        create_engine(GOOD_URL).connect()


@pytest.mark.usefixtures("allowed_target")
def test_only_the_verified_database_target_is_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    reached: list[dict[str, Any]] = []

    def record(*args: Any, **kwargs: Any) -> str:
        reached.append(kwargs)
        return "connected"

    # 先換成記錄用替身，再安裝封鎖：被放行時只會打到替身，不會真的連線。
    monkeypatch.setattr(psycopg, "connect", record)
    monkeypatch.setattr(psycopg.Connection, "connect", record)
    install_network_block(monkeypatch, lambda: m1_isolation.ACTIVE_TARGET)

    assert psycopg.connect(host="127.0.0.1", port=55433, dbname="ig_test") == "connected"
    assert psycopg.connect("host=127.0.0.1 port=55433 dbname=ig_test") == "connected"
    assert len(reached) == 2

    for denied in (
        {"host": "127.0.0.1", "port": 5432},  # 同機其他連接埠
        {"host": "localhost", "port": 6543},
        {"host": "db.prod-example.supabase.co", "port": 55433},  # 同連接埠、其他主機
        {"host": "127.0.0.1", "port": 55433, "hostaddr": "203.0.113.9"},  # hostaddr 繞道
        {"service": "prod"},
        {},
    ):
        with pytest.raises(AssertionError):
            psycopg.connect(**denied)
    with pytest.raises(AssertionError):
        psycopg.connect(PROD_LIKE_URL)
    with pytest.raises(AssertionError):
        create_engine(OTHER_PORT_URL).connect()
    assert len(reached) == 2  # 被拒絕的呼叫沒有到達底層連線


@pytest.mark.usefixtures("allowed_target")
def test_authorised_target_does_not_open_other_network_paths() -> None:
    with pytest.raises(AssertionError):
        socket.create_connection(("127.0.0.1", 55433))  # create_connection 仍被封鎖
    with pytest.raises(AssertionError):
        socket.socket().connect(("203.0.113.10", 55433))
    with pytest.raises(AssertionError):
        socket.socket().connect_ex(("127.0.0.1", 55433))


def test_socketpair_still_works_for_windows_event_loops() -> None:
    first, second = socket.socketpair()
    first.close()
    second.close()


# --- 四、正式 DATABASE_URL 與其他機密不會被使用或傳給子程序 ---


def test_seed_subprocess_environment_is_a_strict_whitelist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in (
        "DATABASE_URL",
        "DATABASE_URL_UNPOOLED",
        "SUPABASE_SECRET_KEY",
        "S3_SECRET_ACCESS_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "VERCEL_TOKEN",
        "CRON_SECRET",
    ):
        monkeypatch.setenv(name, "must-not-leak")
    monkeypatch.setenv("DATABASE_URL", PROD_LIKE_URL)
    url = m1_isolation.validate_url(GOOD_URL)

    env = m1_isolation.seed_environment(tmp_path, url)

    assert set(env) == {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PATH", m1_isolation.SEED_URL_ENV}
    assert env[m1_isolation.SEED_URL_ENV] == GOOD_URL
    assert PROD_LIKE_URL not in env.values()
    assert "must-not-leak" not in env.values()


def test_production_database_url_cannot_become_the_isolated_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect = fake_connect()
    monkeypatch.setenv("DATABASE_URL", PROD_LIKE_URL)

    with pytest.raises(m1_isolation.UnsafeTargetError):
        m1_isolation.load_target(
            {m1_isolation.URL_ENV: PROD_LIKE_URL, m1_isolation.MARKER_ENV: MARKER}, connect
        )

    connect.assert_not_called()
