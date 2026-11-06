"""
模組名稱：tests.unit.test_busy_manager_node
功能說明：用 Node 內建 assert 執行 busy.js（擁有者／Token 管理）與 main.js（表單、HTMX 事件處理）的遮罩單元測試。
注意事項：
    - 沒有安裝 node 時略過；不需要任何 npm 套件，測試腳本在 tests/unit/js/。
    - 這是假 DOM 的單元測試，不是瀏覽器或 HTMX 的實際行為驗證（那部分另用本機瀏覽器測試頁驗證）。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parent / "js"
SCRIPTS = {
    "busy_manager.test.js": "15/15 passed",  # busy.js：擁有者 Token、對話框重新安置
    "main_busy.test.js": "35/35 passed",  # main.js：表單、HTMX、側邊欄導覽、按鈕載入與防重
}


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能執行前端單元測試")
@pytest.mark.parametrize("script", sorted(SCRIPTS))
def test_busy_overlay_node_scripts(script: str) -> None:
    result = subprocess.run(
        ["node", str(JS_DIR / script)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert SCRIPTS[script] in result.stdout
