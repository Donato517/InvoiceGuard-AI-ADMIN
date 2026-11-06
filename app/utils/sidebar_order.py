"""模組名稱：app.utils.sidebar_order
功能說明：側欄功能的群組與順序（2026-11-05 使用者核定）；只分組、重排已授權的項目，不新增、不改權限。
注意事項：
    - 側欄項目來自部門授權（admin_login_service.sidebar_menu），這裡只在樣板顯示前分組排序。
    - 尚未接線的功能沒有固定代碼（使用 DB 的 function_code），因此以名稱比對。
    - 未列出的功能放「其他作業」，系統管理項目放最後的「系統管理」；沒有任何項目的群組不顯示。
    - 「部門功能設定」不單獨顯示（從部門管理頁進入），沿用原本側欄的排除規則。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


class _Entry(Protocol):
    code: str
    name: str


@dataclass(frozen=True)
class SidebarGroup[EntryT: _Entry]:
    """側欄群組。

    Attributes:
        key: 群組代碼（瀏覽器記住開合狀態用）。
        title: 群組名稱。
        icon: 群組圖示（emoji）。
        entries: 群組內已授權的項目，依核定順序。
    """

    key: str
    title: str
    icon: str
    entries: tuple[EntryT, ...]


# 使用者核定的群組與順序：(群組代碼, 名稱, 圖示, ((功能代碼, 功能名稱), ...))
# 2026-11-06：「企業基本作業」改名「系統作業」，本人資訊改名「基本資料」放在此群組；
# 「範例發票參考」（showcase 取用頁）放在供應商作業。群組代碼不變（瀏覽器記住的開合狀態沿用）。
_GROUPS: tuple[tuple[str, str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "company",
        "系統作業",
        "🏛️",
        (("company.profile", "公司基本資料"), ("account.profile", "基本資料")),
    ),
    (
        "supplier",
        "供應商作業",
        "🏢",
        (
            ("suppliers", "供應商管理"),
            ("supplier.showcase-reference", "範例發票參考"),
            ("", "範例發票"),
        ),
    ),
    ("invoice", "發票作業", "📤", (("inv.upload", "發票上傳"), ("inv.manage", "發票管理"))),
    (
        "finance",
        "財會作業",
        "🔍",
        (("inv.review", "財會檢核"), ("inv.filing", "申報資料"), ("inv.voucher", "會計傳票")),
    ),
    ("rag", "RAG 知識庫作業", "📚", (("rag.manager", "RAG知識庫管理"), ("", "RAG知識庫問答"))),
)
_OTHER_GROUP = ("other", "其他作業", "🧩")
_SYSTEM_GROUP = ("system", "系統管理", "⚙️")
# 系統管理項目維持既有「部門、功能、帳號」順序
_SYSTEM_ORDER = ("sys.departments", "sys.features", "sys.accounts")
_HIDDEN_CODES = frozenset({"sys.department-permissions"})


@dataclass(frozen=True)
class FixedEntry:
    """不經部門功能目錄、登入後固定顯示的側欄項目（頁面本身另有驗權）。"""

    code: str
    name: str
    route: str | None
    status: str = ""


# 原本寫死在工作台版面（layouts/e_admin.html）的兩個連結，2026-11-06 起放進群組；顯示範圍維持原狀：
# - 基本資料（/account，原「本人資訊」）只在工作台版面顯示（只有歡迎頁權限的帳號開不了，不放進一般側欄）
# - 範例發票參考（/showcases/reference）：工作台版面照舊顯示；一般側欄在有「供應商管理」時顯示，頁面另依部門權限驗證
ACCOUNT_ENTRY = FixedEntry("account.profile", "基本資料", "/account")
SHOWCASE_REFERENCE_ENTRY = FixedEntry(
    "supplier.showcase-reference", "範例發票參考", "/showcases/reference"
)


def _normalize(name: str) -> str:
    return "".join(name.split()).casefold()


def _slot(entry: _Entry) -> tuple[int, int]:
    """回傳 (群組序號, 群組內序號)：先比代碼，再比名稱（忽略空白與大小寫）。"""
    for group_index, (_, _, _, members) in enumerate(_GROUPS):
        for index, (code, name) in enumerate(members):
            if (code and entry.code == code) or _normalize(entry.name) == _normalize(name):
                return group_index, index
    if entry.code in _SYSTEM_ORDER:
        return len(_GROUPS) + 1, _SYSTEM_ORDER.index(entry.code)
    return len(_GROUPS), 0


def order_sidebar[EntryT: _Entry](entries: Iterable[EntryT]) -> list[EntryT]:
    """依核定順序排列側欄項目；同名次維持原順序（穩定排序）。

    Args:
        entries: 已授權的側欄項目（含 code、name）。

    Returns:
        排序後的新清單；不改變項目內容。
    """
    return sorted(entries, key=_slot)


def group_sidebar(entries: Iterable[_Entry], workspace: bool = False) -> list[SidebarGroup[_Entry]]:
    """把已授權的側欄項目分到核定群組；沒有項目的群組不回傳。

    Args:
        entries: 已授權的側欄項目（含 code、name）。
        workspace: 是否為工作台版面（layouts/e_admin.html）；是的話加入基本資料與範例發票參考。
            一般側欄只在已有「供應商管理」時加入範例發票參考。代碼已存在的不重複加入。

    Returns:
        依核定順序的群組；「部門功能設定」不顯示。
    """
    items: list[_Entry] = list(entries)
    present = {entry.code for entry in items}
    extras = (
        [ACCOUNT_ENTRY, SHOWCASE_REFERENCE_ENTRY]
        if workspace
        else [SHOWCASE_REFERENCE_ENTRY]
        if "suppliers" in present
        else []
    )
    items += [entry for entry in extras if entry.code not in present]
    definitions = [(key, title, icon) for key, title, icon, _ in _GROUPS]
    definitions += [_OTHER_GROUP, _SYSTEM_GROUP]
    buckets: list[list[_Entry]] = [[] for _ in definitions]
    for entry in order_sidebar(items):
        if entry.code not in _HIDDEN_CODES:
            buckets[_slot(entry)[0]].append(entry)
    return [
        SidebarGroup(key, title, icon, tuple(bucket))
        for (key, title, icon), bucket in zip(definitions, buckets, strict=True)
        if bucket
    ]


def is_active_route(route: str | None, path: str, active_route: str | None = None) -> bool:
    """判斷側欄項目是否為目前頁面（與 sidebar_entry.html 原本的判斷相同）。

    Args:
        route: 項目網址；尚未接線的項目為 None。
        path: 目前請求路徑。
        active_route: 頁面指定的側欄網址（request.state.active_sidebar_route）；有值時以它為準。

    Returns:
        是否為目前頁面。
    """
    if not route:
        return False
    if active_route:
        return active_route == route
    return path == route or path.startswith(route + "/")
