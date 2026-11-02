"""整合測試共用設定：只連「經驗證的隔離測試 PostgreSQL」，其餘外部連線一律封鎖。

注意事項：
    - 需設定 ISOLATED_CASE_DB_URL 與 ISOLATED_CASE_DB_MARKER（見 m1_isolation.py）：URL 必須是字面 127.0.0.1／::1、
      資料庫名稱以 ig_test 開頭，且資料庫內的受控標記與環境變數相符；任一不符直接失敗、不執行測試。
      未設定 URL 時整合測試乾淨略過。
    - 根 conftest 的 Python 層連線封鎖仍然有效：本目錄只額外放行 host、port、dbname、user 四項都與
      已驗證身分相同的 SQLAlchemy／psycopg 連線；Python socket 一律拒絕，Windows 的 socketpair 行為保留。
      這不是 OS／容器層隔離。
    - 正式 DATABASE_URL、Storage、Token 已由根 conftest 在匯入時自環境移除，且不傳給播種子程序。
"""

import os
from collections.abc import Iterator

import m1_isolation
import pytest
from network_guard import install_network_block
from sqlalchemy import Engine, create_engine


@pytest.fixture(autouse=True)
def block_external_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """覆寫根 conftest 的同名 fixture：同樣封鎖，只放行已驗證的隔離資料庫目標。"""
    install_network_block(monkeypatch, lambda: m1_isolation.ACTIVE_TARGET)


@pytest.fixture(scope="session")
def isolated_url() -> Iterator[m1_isolation.URL]:
    try:
        url = m1_isolation.load_target(os.environ)
    except m1_isolation.UnsafeTargetError as error:
        pytest.fail(f"隔離資料庫不安全，拒絕執行：{error}", pytrace=False)
    if url is None:
        pytest.skip(f"未設定 {m1_isolation.URL_ENV}；略過隔離資料庫整合測試")
    m1_isolation.ACTIVE_TARGET = m1_isolation.identity_of(url)
    # 連線標記驗證通過後立刻安裝 session 層封鎖：module／session fixture（播種、清理）也只能連已驗證目標，
    # 不會落在每個測試才安裝的 function 層封鎖之外。
    with pytest.MonkeyPatch.context() as patch:
        install_network_block(patch, lambda: m1_isolation.ACTIVE_TARGET)
        try:
            yield url
        finally:
            m1_isolation.ACTIVE_TARGET = None


@pytest.fixture(scope="session")
def isolated_engine(isolated_url: m1_isolation.URL) -> Iterator[Engine]:
    engine = create_engine(isolated_url)
    yield engine
    engine.dispose()
