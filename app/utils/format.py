"""
模組名稱：app.utils.format
功能說明：畫面顯示格式化（金額、台灣時間）。於 app.core.templating 註冊為 Jinja2 filter。
注意事項：
    - API 回傳的時間為 UTC（ISO 8601），一律在此轉為台灣時間顯示。
    - API 回傳的金額為字串，轉 Decimal 後格式化，避免浮點誤差。
    - 台灣無日光節約時間，以固定 UTC+8 表示，不需依賴 tzdata。
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

TAIPEI_TZ = timezone(timedelta(hours=8), name="Asia/Taipei")

# 空值或格式錯誤時顯示的文字
EMPTY = "-"


def format_currency(value: str | int | Decimal | None) -> str:
    """格式化新台幣金額。

    Returns:
        例如 "1234.5" → "NT$ 1,234.5"、"1000" → "NT$ 1,000"；空值或格式錯誤回傳 "-"。
    """
    if value is None or value == "":
        return EMPTY
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return EMPTY
    # 固定兩位小數後去掉多餘的 0，例如 1,234.50 → 1,234.5
    text = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return f"NT$ {text}"


def format_date(value: str | datetime | None) -> str:
    """格式化日期（台灣時間）。

    Returns:
        例如 "2026-10-20T16:30:00Z" → "2026/10/21"；空值回傳 "-"。
    """
    taipei = _to_taipei(value)
    return taipei.strftime("%Y/%m/%d") if taipei else EMPTY


def format_datetime(value: str | datetime | None) -> str:
    """格式化日期時間（台灣時間，24 小時制）。

    Returns:
        例如 "2026-10-20T16:30:00Z" → "2026/10/21 00:30"；空值回傳 "-"。
    """
    taipei = _to_taipei(value)
    return taipei.strftime("%Y/%m/%d %H:%M") if taipei else EMPTY


def _to_taipei(value: str | datetime | None) -> datetime | None:
    """將 ISO 8601 字串或 datetime 轉為台灣時間；空值、格式錯誤或沒有時區資訊時回傳 None。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(TAIPEI_TZ)
