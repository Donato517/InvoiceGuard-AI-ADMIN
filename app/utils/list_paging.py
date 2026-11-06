"""模組名稱：app.utils.list_paging
功能說明：列表分頁（2026-11-06 使用者核定，發票列表每頁 30 筆）——切出目前頁、計算頁碼與保留篩選條件的網址。
注意事項：
    - 只處理已篩選、排序好的清單；統計數字由呼叫端用完整清單計算，不受分頁影響。
    - 頁碼超出範圍時夾回第 1 頁或最後一頁，不回錯誤。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from urllib.parse import urlencode

PAGE_SIZE = 30


@dataclass(frozen=True)
class PageView[T]:
    """一頁的資料與分頁列所需資訊。

    Attributes:
        items: 目前頁的項目。
        page: 目前頁（從 1 起算）。
        pages: 總頁數（至少 1）。
        total: 全部筆數。
        start: 目前頁第一筆的序號（從 1 起算；沒有資料時為 0）。
        end: 目前頁最後一筆的序號。
        links: 分頁列的頁碼，None 代表省略號。
    """

    items: tuple[T, ...]
    page: int
    pages: int
    total: int
    start: int
    end: int
    links: tuple[int | None, ...]


def page_links(page: int, pages: int) -> tuple[int | None, ...]:
    """第 1 頁、最後一頁與目前頁前後各 1 頁；不連續處以 None（畫面顯示「…」）。"""
    keep = sorted({p for p in (1, pages, page - 1, page, page + 1) if 1 <= p <= pages})
    links: list[int | None] = []
    for index, number in enumerate(keep):
        if index and number - keep[index - 1] > 1:
            links.append(None)
        links.append(number)
    return tuple(links)


def paginate[T](items: Sequence[T], page: int, size: int = PAGE_SIZE) -> PageView[T]:
    """切出指定頁。

    Args:
        items: 已篩選、排序的完整清單。
        page: 要顯示的頁碼；小於 1 或超過總頁數時夾回範圍內。
        size: 每頁筆數。

    Returns:
        PageView：目前頁項目、頁碼資訊與分頁列頁碼。
    """
    total = len(items)
    pages = max(1, ceil(total / size))
    current = min(max(1, page), pages)
    offset = (current - 1) * size
    shown = tuple(items[offset : offset + size])
    return PageView(
        items=shown,
        page=current,
        pages=pages,
        total=total,
        start=offset + 1 if shown else 0,
        end=offset + len(shown),
        links=page_links(current, pages),
    )


def page_url(path: str, page: int, **params: str | None) -> str:
    """產生換頁網址，保留有值的篩選條件（例：?q=…&status=…&page=2）；第 1 頁不帶 page。"""
    query = {key: value for key, value in params.items() if value}
    if page > 1:
        query["page"] = str(page)
    return f"{path}?{urlencode(query)}" if query else path
