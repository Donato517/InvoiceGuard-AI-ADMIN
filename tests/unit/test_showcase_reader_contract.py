"""Show Case 目前版讀取契約須在 ADMIN 與 API 同步。"""

from pathlib import Path

import pytest


def test_showcase_current_reader_matches_sibling() -> None:
    """兩站都存在時比對完整查詢與欄位轉換，避免取用資格分歧。"""
    root = Path(__file__).resolve().parents[2]
    sibling = root.parent / "InvoiceGuard-AI-API"
    if not (sibling / "app").is_dir():
        pytest.skip("單站 checkout 沒有 API")
    relative = Path("app/db/repositories/showcase_v2_reader.py")
    assert (root / relative).read_bytes() == (sibling / relative).read_bytes()
