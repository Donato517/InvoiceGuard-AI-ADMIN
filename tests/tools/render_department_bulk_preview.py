"""模組名稱：tests.tools.render_department_bulk_preview；功能說明：產生純合成的批次勾選瀏覽器測試頁。

不使用實際站台、帳號或 DB；採既有資料替身及封鎖外部連線 fixture。
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load_module(name: str, path: Path) -> ModuleType:
    """載入既有離線 fixture，不執行測試或操作正式資料。"""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("無法載入離線 fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    """只將合成 HTML 寫入忽略版控的 tmp。"""
    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root))
    conftest = load_module("bulk_offline_conftest", project_root / "tests/conftest.py")
    fixtures = load_module(
        "bulk_detail_fixtures", project_root / "tests/unit/test_e_matrix_pages.py"
    )
    output = project_root / "tmp/e-department-bulk/detail.html"
    output.parent.mkdir(parents=True, exist_ok=True)
    patch = pytest.MonkeyPatch()
    try:
        conftest.block_external_services.__wrapped__(patch)
        session = fixtures.db.__wrapped__(patch)
        browser = fixtures.browser.__wrapped__(session, patch)
        try:
            client = next(browser)
            response = client.get(fixtures.PATH)
            if response.status_code != 200:
                raise RuntimeError("合成內容頁未能產生")
            output.write_text(response.text, encoding="utf-8")
        finally:
            browser.close()
    finally:
        patch.undo()
    print("Synthetic department preview ready; real DB and network blocked")


if __name__ == "__main__":
    main()
