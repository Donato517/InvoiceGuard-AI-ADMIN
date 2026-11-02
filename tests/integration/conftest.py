"""整合測試共用設定：只連「隔離的本機測試 PostgreSQL」，其餘一律略過。

注意事項：
    - 需設定 ISOLATED_CASE_DB_URL（例如 postgresql+psycopg://…@127.0.0.1:55432/ig_test，已套用 Schema v1.8
      去除 pgvector 的測試庫）；主機不是本機回送位址時直接拒絕，避免誤連正式 DB。
    - 此目錄覆寫根 conftest 的網路封鎖 fixture，只因要連本機測試庫；不會連任何遠端服務。
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@pytest.fixture(autouse=True)
def block_external_services() -> None:
    """覆寫根 conftest 的封鎖：整合測試允許連線，但連線目標由 isolated_engine 限制為本機。"""


@pytest.fixture(scope="session")
def isolated_engine() -> Iterator[Engine]:
    raw = os.environ.get("ISOLATED_CASE_DB_URL", "")
    if not raw:
        pytest.skip("未設定 ISOLATED_CASE_DB_URL；略過隔離資料庫整合測試")
    if make_url(raw).host not in LOCAL_HOSTS:
        pytest.fail("ISOLATED_CASE_DB_URL 必須指向本機回送位址，拒絕連線")
    engine = create_engine(raw)
    yield engine
    engine.dispose()
