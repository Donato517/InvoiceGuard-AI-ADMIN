"""模組名稱：tests.tools.render_sidebar_logout_preview；功能說明：離線合成側欄視覺驗收頁。"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load(name: str, path: Path) -> ModuleType:
    """只載入現有合成資料 fixture，沿用封鎖外部服務機制。"""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load offline fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    """輸出忽略版控的合成頁面，不讀實際使用者 Cookie 或資料庫。"""
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    conftest = load("sidebar_conftest", root / "tests/conftest.py")
    fixtures = load("sidebar_login_fixtures", root / "tests/unit/test_department_login.py")
    patch = pytest.MonkeyPatch()
    output = root / "tmp/sidebar-logout"
    output.mkdir(parents=True, exist_ok=True)
    try:
        conftest.block_external_services.__wrapped__(patch)
        state = fixtures.state.__wrapped__(fixtures.encoded_password.__wrapped__())
        state["functions"][-1]["function_name"] = "(21)三聯式統一發票"
        module = fixtures.module.__wrapped__(state)
        browser = fixtures.browser.__wrapped__(module, patch)
        try:
            client = next(browser)
            fixtures.login(client)
            for name, url in [("welcome", "/welcome"), ("workspace", "/workspace")]:
                page = client.get(url)
                if page.status_code != 200:
                    raise RuntimeError("Synthetic page could not be rendered")
                (output / f"{name}.html").write_text(page.text, encoding="utf-8")
        finally:
            browser.close()
    finally:
        patch.undo()
    print("Synthetic sidebar pages ready; production DB and external network blocked")


if __name__ == "__main__":
    main()
