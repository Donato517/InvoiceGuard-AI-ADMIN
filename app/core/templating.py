"""
模組名稱：app.core.templating
功能說明：建立 Jinja2 樣板環境，註冊全站共用的 filter 與全域變數。
注意事項：
    - .html 樣板預設開啟自動跳脫（autoescape）以防止 XSS；禁止對資料庫資料使用 |safe。
    - 靜態檔網址一律使用 static_url()，會自動附上版本參數，部署新版後瀏覽器才會重新下載。
    - 已登入頁面可在樣板中以 request.state.admin 取得目前管理者（由 core/deps.py 設定）。
"""

from datetime import datetime

from fastapi.templating import Jinja2Templates

from app.constants import (
    INVOICE_STATUS_LABEL,
    JOB_NAME_LABEL,
    JOB_STATUS_LABEL,
    JOB_TRIGGER_LABEL,
    MENU_ITEMS,
    SITE_NAME,
)
from app.core.config import BASE_DIR, settings
from app.utils.format import TAIPEI_TZ, format_currency, format_date, format_datetime

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


def static_url(path: str) -> str:
    """產生靜態檔網址。

    Args:
        path: 相對於 app/static/ 的路徑，例如 "css/main.css"。

    Returns:
        例如 "/static/css/main.css?v=a1b2c3d"。
    """
    return f"/static/{path}?v={settings.static_version}"


def current_year() -> int:
    """取得目前台灣時間的年份（頁尾版權用）。"""
    return datetime.now(TAIPEI_TZ).year


# ---- 樣板全域變數 / 函式：樣板中可直接使用 ----
templates.env.globals.update(
    SITE_NAME=SITE_NAME,
    MENU_ITEMS=MENU_ITEMS,
    INVOICE_STATUS_LABEL=INVOICE_STATUS_LABEL,
    JOB_NAME_LABEL=JOB_NAME_LABEL,
    JOB_TRIGGER_LABEL=JOB_TRIGGER_LABEL,
    JOB_STATUS_LABEL=JOB_STATUS_LABEL,
    static_url=static_url,
    current_year=current_year,
)

# ---- 樣板 filter：{{ value | currency }} ----
templates.env.filters.update(
    currency=format_currency,
    taipei_date=format_date,
    taipei_datetime=format_datetime,
)
