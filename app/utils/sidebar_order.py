"""模組名稱：app.utils.sidebar_order
功能說明：側欄功能的顯示順序（2026-11-05 使用者核定）；只重排已授權的項目，不新增、不移除、不改權限。
注意事項：
    - 側欄項目來自部門授權（admin_login_service.sidebar_menu），這裡只在樣板顯示前排序。
    - 尚未接線的功能沒有固定代碼（使用 DB 的 function_code），因此以名稱比對。
    - 未列出的功能排在固定順序之後、系統管理項目之前，彼此維持原本順序；帳號管理固定在最後，緊接登出。
"""

from collections.abc import Iterable
from typing import Protocol

# 使用者核定的業務功能順序：代碼或名稱 → 名次
_BUSINESS_ORDER = (
    ("company.profile", "公司基本資料"),
    ("suppliers", "供應商管理"),
    ("", "範例發票"),
    ("inv.upload", "發票上傳"),
    ("inv.manage", "發票管理"),
    ("inv.review", "財會檢核"),
    ("inv.filing", "申報資料"),
    ("inv.voucher", "會計傳票"),
    ("rag.manager", "RAG知識庫管理"),
    ("", "RAG知識庫問答"),
)
# 系統管理項目接在業務功能之後，維持既有「部門、功能、帳號」順序（帳號之後是登出）
_SYSTEM_ORDER = ("sys.departments", "sys.features", "sys.accounts")
_OTHER_RANK = len(_BUSINESS_ORDER)


class _Entry(Protocol):
    code: str
    name: str


def _normalize(name: str) -> str:
    return "".join(name.split()).casefold()


def _rank(entry: _Entry) -> int:
    """回傳項目名次：先比代碼，再比名稱（忽略空白與大小寫）。"""
    for index, (code, name) in enumerate(_BUSINESS_ORDER):
        if (code and entry.code == code) or _normalize(entry.name) == _normalize(name):
            return index
    if entry.code in _SYSTEM_ORDER:
        return _OTHER_RANK + 1 + _SYSTEM_ORDER.index(entry.code)
    return _OTHER_RANK


def order_sidebar[EntryT: _Entry](entries: Iterable[EntryT]) -> list[EntryT]:
    """依核定順序排列側欄項目；同名次維持原順序（穩定排序）。

    Args:
        entries: 已授權的側欄項目（含 code、name）。

    Returns:
        排序後的新清單；不改變項目內容。
    """
    return sorted(entries, key=_rank)
