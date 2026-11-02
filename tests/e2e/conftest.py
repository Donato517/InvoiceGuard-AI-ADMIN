"""E2E-1 隔離雙服務測試的環境：本機 PostgreSQL（TLS）＋真實 API 服務＋真實 ADMIN 服務。

預設不執行（整個目錄 skip）；需設定 E2E_ENABLED=1。所有連線只限回送位址；
每次執行使用隨機產生的測試密碼與臨時憑證，不讀取任何公司機密或正式設定。
外部服務（Document AI、Groq、Storage）與 ADMIN 的『新增發票』授權是測試替身／測試設定，見 fakes.py 與 launcher。
"""

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

HERE = Path(__file__).resolve().parent
ADMIN_ROOT = HERE.parents[1]
PROJECT_ROOT = ADMIN_ROOT.parent
API_ROOT = Path(os.environ.get("E2E_API_ROOT", PROJECT_ROOT / "InvoiceGuard-AI-API"))
SCHEMA_SQL = Path(
    os.environ.get(
        "E2E_SCHEMA_SQL",
        PROJECT_ROOT
        / "20261101_InvoiceGuard-STARTUP/InvoiceGuard_AI_PostgreSQL_Schema_v1_8_PortableAuth_draft.sql",
    )
)
# 預設埠與容器名；其他測試堆疊占用時可用環境變數改（皆只限回送位址）
PG_PORT = int(os.environ.get("E2E_PG_PORT", "55433"))
API_PORT = int(os.environ.get("E2E_API_PORT_BASE", "18000"))
ADMIN_PORT = int(os.environ.get("E2E_ADMIN_PORT_BASE", "18002"))
CONTAINER = os.environ.get("E2E_CONTAINER", "ig-e2e-pg")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("E2E_ENABLED") == "1":
        return
    skip = pytest.mark.skip(reason="E2E 預設不執行；設定 E2E_ENABLED=1 才會啟動隔離雙服務")
    for item in items:
        if HERE in Path(str(item.fspath)).parents:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def block_external_services() -> None:
    """覆寫根 conftest 的封鎖：E2E 需要連本機資料庫與本機服務（目標由 Stack 限制為回送位址）。"""


def _run(*args: str, check: bool = True, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check, **kwargs)  # noqa: S603


def _port_free(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


class Stack:
    """隔離環境：PostgreSQL 容器、API 行程、ADMIN 行程與共用工作目錄。"""

    def __init__(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="ig-e2e-"))
        (self.work / "flags").mkdir()
        (self.work / "storage").mkdir()
        self.db_password = secrets.token_hex(12)
        self.db_url = f"postgresql+psycopg://postgres:{self.db_password}@127.0.0.1:{PG_PORT}/ig_e2e?sslmode=require"
        self.api_url = f"http://127.0.0.1:{API_PORT}"
        self.admin_url = f"http://127.0.0.1:{ADMIN_PORT}"
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.engine: Engine | None = None

    # ---- PostgreSQL ----

    def start_database(self) -> None:
        for port in (PG_PORT, API_PORT, ADMIN_PORT):
            assert _port_free(port), f"埠 {port} 已被占用"
        certs = self.work / "certs"
        certs.mkdir()
        _run(
            "openssl",
            "req",
            "-new",
            "-x509",
            "-days",
            "2",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-keyout",
            str(certs / "server.key"),
            "-out",
            str(certs / "server.crt"),
        )
        _run("docker", "rm", "-f", CONTAINER, check=False)
        script = (
            "cp /certs/server.crt /certs/server.key /var/lib/postgresql/ && "
            "chown postgres:postgres /var/lib/postgresql/server.* && chmod 600 /var/lib/postgresql/server.key && "
            "exec docker-entrypoint.sh postgres -c ssl=on "
            "-c ssl_cert_file=/var/lib/postgresql/server.crt -c ssl_key_file=/var/lib/postgresql/server.key"
        )
        _run(
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER,
            "-e",
            f"POSTGRES_PASSWORD={self.db_password}",
            "-e",
            "POSTGRES_DB=ig_e2e",
            "-p",
            f"127.0.0.1:{PG_PORT}:5432",
            "-v",
            f"{certs}:/certs:ro",
            "--entrypoint",
            "bash",
            "postgres:15",
            "-c",
            script,
        )
        self.engine = create_engine(self.db_url)
        deadline = time.monotonic() + 60
        ok = 0
        while time.monotonic() < deadline:
            try:
                with self.engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                ok += 1
                if ok >= 3:  # 容器初始化會重啟一次，連續成功才算就緒
                    break
            except Exception:
                ok = 0
            time.sleep(1)
        else:
            raise RuntimeError("PostgreSQL 沒有在時限內就緒")

    def load_schema(self) -> None:
        sql = SCHEMA_SQL.read_text(encoding="utf-8")
        sql = re.sub(
            r"CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;\nDO \$vector_check\$.*?\$vector_check\$;",
            "-- pgvector removed for isolated test",
            sql,
            flags=re.S,
        ).replace("extensions.vector(1536)", "text")
        target = self.work / "schema.sql"
        target.write_text(sql, encoding="utf-8")
        _run("docker", "cp", str(target), f"{CONTAINER}:/schema.sql")
        done = _run(
            "docker",
            "exec",
            CONTAINER,
            "psql",
            "-U",
            "postgres",
            "-d",
            "ig_e2e",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-f",
            "/schema.sql",
        )
        assert done.returncode == 0, done.stderr[-1500:]

    def seed(self) -> None:
        """合成測試資料：兩個部門、三個帳號（密碼為本次隨機產生）與功能授權。"""
        sys.path.insert(0, str(ADMIN_ROOT))
        from app.core.security import hash_password  # ADMIN 與 API 同版 Argon2id

        assert self.engine is not None
        with self.engine.begin() as conn:
            dept = {
                code: conn.execute(
                    text(
                        "INSERT INTO public.departments(department_code, department_name) VALUES (:c, :n) RETURNING department_id"
                    ),
                    {"c": code, "n": name},
                ).scalar_one()
                for code, name in (("e2e_procurement", "E2E 採購部"), ("e2e_sales", "E2E 業務部"))
            }
            functions = {
                code: conn.execute(
                    text(
                        "INSERT INTO public.app_functions(function_code, function_name, app_scope) VALUES (:c, :n, 'admin') RETURNING function_id"
                    ),
                    {"c": code, "n": name},
                ).scalar_one()
                for code, name in (
                    ("inv.intake", "新增發票（E2E）"),
                    ("Triplicate Uniform Invoice", "發票（E2E）"),
                )
            }
            grants = [  # (部門, 功能, view, create)
                ("e2e_procurement", "inv.intake", True, True),
                ("e2e_procurement", "Triplicate Uniform Invoice", True, False),
                ("e2e_sales", "inv.intake", True, False),  # 業務部：能看、不能新增
                ("e2e_sales", "Triplicate Uniform Invoice", True, False),
            ]
            for dept_code, function_code, can_view, can_create in grants:
                conn.execute(
                    text(
                        "INSERT INTO public.department_permissions(department_id, function_id, can_view, can_create) VALUES (:d, :f, :v, :c)"
                    ),
                    {
                        "d": dept[dept_code],
                        "f": functions[function_code],
                        "v": can_view,
                        "c": can_create,
                    },
                )
            for name, dept_code in (("buyer", "e2e_procurement"), ("outsider", "e2e_sales")):
                password = secrets.token_urlsafe(18)
                email = f"{name}_{uuid4().hex[:6]}@example.test"
                user = conn.execute(
                    text(
                        "INSERT INTO invoiceguard_identity.user_accounts(email, password_hash, account_status, primary_department_id) VALUES (:e, :h, 'active', :d) RETURNING user_id"
                    ),
                    {"e": email, "h": hash_password(password), "d": dept[dept_code]},
                ).scalar_one()
                conn.execute(
                    text("INSERT INTO public.user_profiles(user_id, display_name) VALUES (:u, :n)"),
                    {"u": user, "n": f"E2E {name}"},
                )
                conn.execute(
                    text(
                        "INSERT INTO public.user_departments(user_id, department_id) VALUES (:u, :d)"
                    ),
                    {"u": user, "d": dept[dept_code]},
                )
                self.users[name] = {
                    "user_id": str(user),
                    "email": email,
                    "password": password,
                    "department_id": str(dept[dept_code]),
                }

    # ---- 服務行程 ----

    def _env(self, extra: dict[str, str]) -> dict[str, str]:
        base = {
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(self.work),
            "E2E_WORK": str(self.work),
            "DATABASE_URL": self.db_url,
            "APP_ENV": "local",
            "E2E_API_PORT": str(API_PORT),
            "E2E_ADMIN_PORT": str(ADMIN_PORT),
            "E2E_API_BASE": self.api_url,
        }
        return {**base, **extra}

    def start_api(self) -> None:
        log = (self.work / "api.log").open("ab")
        self.processes["api"] = subprocess.Popen(  # noqa: S603
            [str(API_ROOT / ".venv/bin/python"), "-B", str(HERE / "api_launcher.py")],
            cwd=API_ROOT,
            env=self._env({"PYTHONPATH": str(API_ROOT)}),
            stdout=log,
            stderr=log,
        )
        self._wait(f"{self.api_url}/health", "api")

    def start_admin(self) -> None:
        log = (self.work / "admin.log").open("ab")
        users = {
            u["user_id"]: {"email": u["email"], "password": u["password"]}
            for u in self.users.values()
        }
        self.processes["admin"] = subprocess.Popen(  # noqa: S603
            [str(ADMIN_ROOT / ".venv/bin/python"), "-B", str(HERE / "admin_launcher.py")],
            cwd=ADMIN_ROOT,
            env=self._env({"PYTHONPATH": str(ADMIN_ROOT), "E2E_USERS_JSON": json.dumps(users)}),
            stdout=log,
            stderr=log,
        )
        self._wait(f"{self.admin_url}/health", "admin")

    def _wait(self, url: str, name: str) -> None:
        import httpx

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.processes[name].poll() is not None:
                raise RuntimeError(
                    f"{name} 啟動失敗：\n"
                    + (self.work / f"{name}.log").read_text(errors="replace")[-3000:]
                )
            try:
                if httpx.get(url, timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError(
            f"{name} 沒有在時限內就緒：\n"
            + (self.work / f"{name}.log").read_text(errors="replace")[-3000:]
        )

    def stop(self, name: str) -> None:
        proc = self.processes.pop(name, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ---- 測試輔助 ----

    def set_flag(self, name: str, value: str = "1") -> None:
        (self.work / "flags" / name).write_text(value, encoding="utf-8")

    def clear_flags(self) -> None:
        for path in (self.work / "flags").glob("*"):
            path.unlink()

    def calls(self, event: str | None = None) -> list[dict[str, Any]]:
        path = self.work / "calls.log"
        rows = (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )
        return [row for row in rows if event is None or row["event"] == event]

    def query(self, sql: str, **params: Any) -> list[Any]:
        assert self.engine is not None
        with self.engine.connect() as conn:
            return list(conn.execute(text(sql), params).mappings())

    def scalar(self, sql: str, **params: Any) -> Any:
        assert self.engine is not None
        with self.engine.connect() as conn:
            return conn.execute(text(sql), params).scalar_one()

    def close(self) -> None:
        for name in list(self.processes):
            self.stop(name)
        _run("docker", "rm", "-f", CONTAINER, check=False)
        shutil.rmtree(self.work, ignore_errors=True)


@pytest.fixture(scope="session")
def stack() -> Iterator[Stack]:
    if os.environ.get("E2E_ENABLED") != "1":
        pytest.skip("E2E 預設不執行")
    instance = Stack()
    try:
        instance.start_database()
        instance.load_schema()
        instance.seed()
        instance.start_api()
        instance.start_admin()
        yield instance
    finally:
        instance.close()


@pytest.fixture(autouse=True)
def reset_flags(request: pytest.FixtureRequest) -> Iterator[None]:
    yield
    if "stack" in request.fixturenames:
        request.getfixturevalue("stack").clear_flags()
