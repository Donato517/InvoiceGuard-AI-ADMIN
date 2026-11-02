"""整合測試隔離防護的負向測試：全部使用替身，不會真的連到任何外部服務或資料庫。

涵蓋：非本機 URL、未標記的本機目標、host／port／dbname／user 四項身分比對、hostaddr／service／
Unix socket／多主機／localhost 繞道、非 PostgreSQL 外連、正式 DATABASE_URL、未設定時略過、
合法身分仍被放行（以替身驗證，不連線）、播種子程序自己的封鎖與環境白名單、Windows socketpair 行為保留。
這些是 Python 層防護的測試，不證明 OS／容器層隔離。
"""

import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import m1_isolation
import psycopg
import pytest
from network_guard import install_network_block
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

MARKER = "m1-test-marker-0123456789"
GOOD_URL = "postgresql+psycopg://postgres:pw@127.0.0.1:55433/ig_test"
OTHER_DB_URL = "postgresql+psycopg://postgres:pw@127.0.0.1:55433/postgres"
OTHER_USER_URL = "postgresql+psycopg://other:pw@127.0.0.1:55433/ig_test"
OTHER_PORT_URL = "postgresql+psycopg://postgres:pw@127.0.0.1:5432/ig_test"
NO_DB_URL = "postgresql+psycopg://postgres:pw@127.0.0.1:55433"
PROD_LIKE_URL = "postgresql+psycopg://u:p@db.prod-example.supabase.co:5432/postgres"
IDENTITY = ("127.0.0.1", 55433, "ig_test", "postgres")
INTEGRATION_DIR = Path(__file__).resolve().parent
SEED = INTEGRATION_DIR / "m1_seed_via_api.py"


def fake_connect(db: str = "ig_test", marker: str | None = MARKER) -> MagicMock:
    """模擬 psycopg.connect：回傳 (資料庫名, 標記)；不會連線。"""
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = (db, marker)
    return MagicMock(return_value=connection)


@pytest.fixture
def allowed_target(monkeypatch: pytest.MonkeyPatch) -> tuple[str, int, str, str]:
    monkeypatch.setattr(m1_isolation, "ACTIVE_TARGET", IDENTITY)
    return IDENTITY


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch, allowed_target: tuple[str, int, str, str]) -> list:
    """先把底層連線換成記錄用替身、再安裝封鎖：被放行的呼叫只會打到替身，不會真的連線。"""
    reached: list[Any] = []

    def record(*args: Any, **kwargs: Any) -> str:
        reached.append((args, kwargs))
        return "connected"

    monkeypatch.setattr(psycopg, "connect", record)
    monkeypatch.setattr(psycopg.Connection, "connect", record)
    monkeypatch.setattr(Engine, "connect", lambda self, *a, **k: record(self.url))
    monkeypatch.setattr(Engine, "raw_connection", lambda self, *a, **k: record(self.url))
    install_network_block(monkeypatch, lambda: m1_isolation.ACTIVE_TARGET)
    return reached


# --- 一、URL 靜態檢查（非本機、驅動、名稱、查詢參數、localhost）---


@pytest.mark.parametrize(
    "raw",
    [
        PROD_LIKE_URL,
        "postgresql+psycopg://u:p@10.0.0.5:5432/ig_test",
        "postgresql+psycopg://u:p@localhost:5432/ig_test",  # localhost 可能解析成不同的 IPv4／IPv6 服務
        "postgresql+psycopg://u:p@127.0.0.1/ig_test",  # 缺連接埠
        "postgresql+psycopg://u:p@127.0.0.1:5432/postgres",  # 名稱前綴不符
        "postgresql+psycopg://127.0.0.1:5432/ig_test",  # 缺使用者
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
    assert m1_isolation.identity_of(url) == IDENTITY


def test_unset_url_means_skip_not_failure() -> None:
    assert m1_isolation.load_target({}, fake_connect()) is None


# --- 三、host／port／dbname／user 四項身分與外部連線封鎖 ---


def test_everything_is_blocked_when_no_database_is_authorised() -> None:
    assert m1_isolation.ACTIVE_TARGET is None  # 未驗證任何目標時，連本機資料庫也不放行

    with pytest.raises(AssertionError):
        socket.create_connection(("example.com", 80))
    with pytest.raises(AssertionError):
        socket.getaddrinfo("example.com", 80)
    with pytest.raises(AssertionError):
        socket.socket().connect(("203.0.113.10", 443))
    with pytest.raises(AssertionError):
        psycopg.connect(host="127.0.0.1", port=55433, user="postgres", dbname="ig_test")
    with pytest.raises(AssertionError):
        create_engine(GOOD_URL).connect()


def test_exact_identity_is_reachable(recorded: list) -> None:
    assert psycopg.connect(host="127.0.0.1", port=55433, dbname="ig_test", user="postgres")
    assert psycopg.connect("host=127.0.0.1 port=55433 dbname=ig_test user=postgres")
    assert psycopg.connect(
        "postgresql://postgres:pw@127.0.0.1:55433/ig_test"  # URI 形式的連線字串
    )
    assert create_engine(GOOD_URL).connect()
    assert create_engine(GOOD_URL).raw_connection()
    assert len(recorded) == 5


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "host": "127.0.0.1",
            "port": 55433,
            "dbname": "postgres",
            "user": "postgres",
        },  # 不同 dbname
        {"host": "127.0.0.1", "port": 55433, "dbname": "ig_test", "user": "other"},  # 不同 user
        {"host": "127.0.0.1", "port": 5432, "dbname": "ig_test", "user": "postgres"},  # 不同 port
        {"host": "::1", "port": 55433, "dbname": "ig_test", "user": "postgres"},  # IPv6 ≠ IPv4
        {"host": "localhost", "port": 55433, "dbname": "ig_test", "user": "postgres"},
        {
            "host": "db.prod-example.supabase.co",
            "port": 55433,
            "dbname": "ig_test",
            "user": "postgres",
        },
        {"host": "127.0.0.1", "port": 55433, "user": "postgres"},  # 缺 dbname
        {"host": "127.0.0.1", "port": 55433, "dbname": "ig_test"},  # 缺 user
        {"host": "127.0.0.1", "dbname": "ig_test", "user": "postgres"},  # 缺 port
        {"port": 55433, "dbname": "ig_test", "user": "postgres"},  # 缺 host（libpq 會讀 PGHOST）
        {},
        {  # hostaddr 繞道
            "host": "127.0.0.1",
            "hostaddr": "203.0.113.9",
            "port": 55433,
            "dbname": "ig_test",
            "user": "postgres",
        },
        {"service": "prod", "dbname": "ig_test", "user": "postgres"},
        {"host": "/var/run/postgresql", "port": 55433, "dbname": "ig_test", "user": "postgres"},
        {"host": "127.0.0.1,203.0.113.9", "port": 55433, "dbname": "ig_test", "user": "postgres"},
        {"host": "127.0.0.1", "port": "55433,5432", "dbname": "ig_test", "user": "postgres"},
    ],
)
def test_any_other_identity_is_denied(recorded: list, kwargs: dict[str, Any]) -> None:
    with pytest.raises(AssertionError):
        psycopg.connect(**kwargs)
    assert recorded == []  # 被拒絕的呼叫沒有到達底層連線


@pytest.mark.parametrize(
    "raw",
    [OTHER_DB_URL, OTHER_USER_URL, OTHER_PORT_URL, NO_DB_URL, PROD_LIKE_URL, GOOD_URL + "?host=x"],
)
def test_sqlalchemy_engines_with_other_identity_are_denied(recorded: list, raw: str) -> None:
    with pytest.raises(AssertionError):
        create_engine(raw).connect()
    with pytest.raises(AssertionError):
        create_engine(raw).raw_connection()
    assert recorded == []


@pytest.mark.parametrize(
    "conninfo",
    [
        "host=127.0.0.1 port=55433 dbname=postgres user=postgres",
        "postgresql://other:pw@127.0.0.1:55433/ig_test",
        "postgresql://postgres:pw@127.0.0.1:55433/ig_test?hostaddr=203.0.113.9",
        "host=127.0.0.1 port=55433 user=postgres",
        PROD_LIKE_URL,
        "not a conninfo ###",
    ],
)
def test_conninfo_strings_with_other_identity_are_denied(recorded: list, conninfo: str) -> None:
    with pytest.raises(AssertionError):
        psycopg.connect(conninfo)
    with pytest.raises(AssertionError):
        psycopg.Connection.connect(conninfo)
    assert recorded == []


@pytest.mark.usefixtures("allowed_target")
def test_python_sockets_stay_blocked_even_for_the_authorised_database() -> None:
    # 此測試的 autouse 封鎖已讀到 ACTIVE_TARGET（資料庫身分已授權），Python socket 仍不放行。
    with pytest.raises(AssertionError):
        socket.create_connection(("127.0.0.1", 55433))
    with pytest.raises(AssertionError):
        socket.socket().connect(("127.0.0.1", 55433))  # libpq 不需要 Python socket
    with pytest.raises(AssertionError):
        socket.socket().connect_ex(("127.0.0.1", 55433))


def test_socketpair_still_works_for_windows_event_loops() -> None:
    first, second = socket.socketpair()
    first.close()
    second.close()


# --- 四、播種子程序：環境白名單與自己的連線封鎖 ---


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

    env = m1_isolation.seed_environment(tmp_path, url, MARKER)

    expected = {
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "PATH",
        m1_isolation.SEED_URL_ENV,
        m1_isolation.MARKER_ENV,
    }
    if m1_isolation.IS_WINDOWS:
        expected.add("SYSTEMROOT")  # 實際在 Windows 上執行時的預期
    assert set(env) == expected
    assert env[m1_isolation.SEED_URL_ENV] == GOOD_URL
    assert env[m1_isolation.MARKER_ENV] == MARKER
    assert PROD_LIKE_URL not in env.values()
    assert "must-not-leak" not in env.values()


def test_windows_environment_adds_only_systemroot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = m1_isolation.validate_url(GOOD_URL)
    monkeypatch.setattr(m1_isolation, "IS_WINDOWS", True)
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("DATABASE_URL", PROD_LIKE_URL)

    env = m1_isolation.seed_environment(tmp_path, url, MARKER)

    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert set(env) == {
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "PATH",
        m1_isolation.SEED_URL_ENV,
        m1_isolation.MARKER_ENV,
        "SYSTEMROOT",
    }
    assert PROD_LIKE_URL not in env.values()


def test_windows_without_systemroot_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(m1_isolation, "IS_WINDOWS", True)
    monkeypatch.delenv("SYSTEMROOT", raising=False)

    with pytest.raises(m1_isolation.UnsafeTargetError, match="SYSTEMROOT"):
        m1_isolation.seed_environment(tmp_path, m1_isolation.validate_url(GOOD_URL), MARKER)


def test_non_windows_environment_has_no_systemroot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(m1_isolation, "IS_WINDOWS", False)
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")

    env = m1_isolation.seed_environment(tmp_path, m1_isolation.validate_url(GOOD_URL), MARKER)

    assert "SYSTEMROOT" not in env


def run_seed_side(code: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """以與播種子程序相同的白名單環境執行一小段程式；全程只有被拒絕的連線嘗試，不會外連。"""
    url = m1_isolation.validate_url(GOOD_URL)
    env = m1_isolation.seed_environment(tmp_path, url, MARKER)
    env["PYTHONPATH"] = ""
    program = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {str(INTEGRATION_DIR)!r})
        sys.path.insert(0, {str(INTEGRATION_DIR.parent)!r})
        import m1_isolation
        url = m1_isolation.validate_url(os.environ[m1_isolation.SEED_URL_ENV])
        m1_isolation.install_process_guard(url)
        """
    )
    return subprocess.run(  # noqa: S603
        [sys.executable, "-B", "-c", program + textwrap.dedent(code)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_seed_process_guard_denies_other_targets_and_python_sockets(tmp_path: Path) -> None:
    done = run_seed_side(
        """
        import socket, psycopg
        from sqlalchemy import create_engine
        attempts = [
            lambda: psycopg.connect(host="127.0.0.1", port=55433, dbname="postgres", user="postgres"),
            lambda: psycopg.connect(host="127.0.0.1", port=55433, dbname="ig_test", user="other"),
            lambda: psycopg.connect(host="127.0.0.1", port=5432, dbname="ig_test", user="postgres"),
            lambda: psycopg.connect(host="db.prod-example.supabase.co", port=5432, dbname="ig_test", user="postgres"),
            lambda: psycopg.connect("host=127.0.0.1 port=55433 dbname=ig_test"),
            lambda: create_engine("postgresql+psycopg://postgres:x@127.0.0.1:55433/postgres").connect(),
            lambda: create_engine("postgresql+psycopg://postgres:x@db.prod-example.supabase.co:5432/postgres").connect(),
            lambda: socket.create_connection(("example.com", 443)),
            lambda: socket.socket().connect(("127.0.0.1", 55433)),
            lambda: socket.getaddrinfo("example.com", 443),
        ]
        denied = 0
        for attempt in attempts:
            try:
                attempt()
            except AssertionError:
                denied += 1
        print("denied", denied, len(attempts))
        print("production-url-visible", "DATABASE_URL" in os.environ)
        """,
        tmp_path,
    )

    assert done.returncode == 0, done.stderr[-800:]
    assert "denied 10 10" in done.stdout
    assert "production-url-visible False" in done.stdout


@pytest.mark.parametrize(
    "overrides",
    [
        {m1_isolation.MARKER_ENV: "short"},  # 標記過短：不連線就拒絕
        {m1_isolation.SEED_URL_ENV: PROD_LIKE_URL},  # 非本機 URL
        {m1_isolation.SEED_URL_ENV: "postgresql+psycopg://u:p@localhost:5432/ig_test"},
    ],
)
def test_seed_script_refuses_before_importing_api_or_writing(
    tmp_path: Path, overrides: dict[str, str]
) -> None:
    env = m1_isolation.seed_environment(tmp_path, m1_isolation.validate_url(GOOD_URL), MARKER)
    env.update(overrides)
    env["PYTHONPATH"] = str(tmp_path)  # 沒有 API：若走到 API 匯入會是 ImportError，而不是被拒絕

    done = subprocess.run(  # noqa: S603
        [sys.executable, "-B", str(SEED), "tag", str(tmp_path / "out.json")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert done.returncode != 0
    assert "UnsafeTargetError" in done.stderr
    assert not (tmp_path / "out.json").exists()


def test_environment_of_the_test_process_has_no_production_database_url() -> None:
    assert os.environ.get("DATABASE_URL") in (None, "")  # 根 conftest 在匯入時已移除
