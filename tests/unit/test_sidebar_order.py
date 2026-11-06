"""測試目標：側欄依使用者核定順序顯示（2026-11-05）；只重排，不新增或移除項目。全部離線。"""

from pathlib import Path

import pytest

from app.core.templating import templates
from app.schemas.e_access import SidebarEntry
from app.utils.sidebar_order import group_sidebar, is_active_route, order_sidebar

ROOT = Path(__file__).resolve().parents[2]


def _entry(code: str, name: str) -> SidebarEntry:
    return SidebarEntry(code, name, None, "")


def test_business_entries_follow_the_approved_order() -> None:
    # 模擬目前 DB 給的順序（RAG 在前、供應商在後、未接線功能以 DB 代碼出現）
    entries = [
        _entry("rag.manager", "RAG知識庫管理"),
        _entry("rag_qa", "RAG知識庫問答"),
        _entry("supplier_examples", "範例發票"),
        _entry("inv.upload", "發票上傳"),
        _entry("inv.manage", "發票管理"),
        _entry("inv.review", "財會檢核"),
        _entry("inv.filing", "申報資料"),
        _entry("inv.voucher", "會計傳票"),
        _entry("company.profile", "公司基本資料"),
        _entry("suppliers", "供應商管理"),
    ]
    assert [e.name for e in order_sidebar(entries)] == [
        "公司基本資料",
        "供應商管理",
        "範例發票",
        "發票上傳",
        "發票管理",
        "財會檢核",
        "申報資料",
        "會計傳票",
        "RAG知識庫管理",
        "RAG知識庫問答",
    ]


def test_unknown_entries_keep_their_order_before_system_entries() -> None:
    entries = [
        _entry("sys.accounts", "帳號管理"),
        _entry("x2", "其他二"),
        _entry("sys.departments", "部門管理"),
        _entry("inv.upload", "發票上傳"),
        _entry("x1", "其他一"),
        _entry("sys.features", "功能目錄"),
    ]
    assert [e.code for e in order_sidebar(entries)] == [
        "inv.upload",
        "x2",
        "x1",
        "sys.departments",
        "sys.features",
        "sys.accounts",
    ]


def test_ordering_neither_adds_nor_removes_entries() -> None:
    entries = [_entry("suppliers", "供應商管理"), _entry("a", "A"), _entry("a", "A")]
    assert sorted(order_sidebar(entries), key=id) == sorted(entries, key=id)


def test_filter_is_registered_for_templates() -> None:
    assert templates.env.filters["sidebar_order"] is order_sidebar


def test_suppliers_are_no_longer_pinned_next_to_logout() -> None:
    logout = (ROOT / "app/templates/partials/sidebar_logout.html").read_text(encoding="utf8")
    assert "suppliers" not in logout
    for path in ("app/templates/partials/sidebar.html", "app/templates/layouts/e_admin.html"):
        assert 'include "partials/sidebar_groups.html"' in (ROOT / path).read_text(encoding="utf8")


# ---- 手風琴群組（2026-11-05）----


def _names(groups: list) -> list[tuple[str, list[str]]]:
    return [(g.title, [e.name for e in g.entries]) for g in groups]


def test_entries_are_grouped_in_the_approved_groups() -> None:
    entries = [
        _entry("rag_qa", "RAG知識庫問答"),
        _entry("inv.voucher", "會計傳票"),
        _entry("inv.upload", "發票上傳"),
        _entry("supplier_examples", "範例發票"),
        _entry("company.profile", "公司基本資料"),
        _entry("inv.review", "財會檢核"),
        _entry("suppliers", "供應商管理"),
        _entry("inv.manage", "發票管理"),
        _entry("rag.manager", "RAG知識庫管理"),
        _entry("inv.filing", "申報資料"),
    ]
    # 2026-11-06：群組改名「系統作業」；有供應商管理時，範例發票參考接在後面
    assert _names(group_sidebar(entries)) == [
        ("系統作業", ["公司基本資料"]),
        ("供應商作業", ["供應商管理", "範例發票參考", "範例發票"]),
        ("發票作業", ["發票上傳", "發票管理"]),
        ("財會作業", ["財會檢核", "申報資料", "會計傳票"]),
        ("RAG 知識庫作業", ["RAG知識庫管理", "RAG知識庫問答"]),
    ]


def test_workspace_layout_adds_basic_profile_and_showcase_reference() -> None:
    """工作台版面原本寫死的「本人資訊」（改名基本資料）與範例發票參考，改放進群組。"""
    groups = group_sidebar([_entry("company.profile", "公司基本資料")], workspace=True)
    assert _names(groups) == [
        ("系統作業", ["公司基本資料", "基本資料"]),
        ("供應商作業", ["範例發票參考"]),
    ]
    routes = {e.name: e.route for g in groups for e in g.entries}
    assert routes["基本資料"] == "/account" and routes["範例發票參考"] == "/showcases/reference"


def test_general_sidebar_never_adds_the_account_page() -> None:
    """只有歡迎頁權限的帳號開不了 /account，一般側欄不加入基本資料。"""
    for entries in ([], [_entry("suppliers", "供應商管理")]):
        names = [e.name for g in group_sidebar(entries) for e in g.entries]
        assert "基本資料" not in names


def test_showcase_reference_needs_supplier_management_in_general_sidebar() -> None:
    assert group_sidebar([_entry("inv.upload", "發票上傳")])[0].key == "invoice"
    names = [e.name for g in group_sidebar([_entry("suppliers", "供應商管理")]) for e in g.entries]
    assert names == ["供應商管理", "範例發票參考"]


def test_workspace_layout_has_no_hardcoded_links_left() -> None:
    layout = (ROOT / "app/templates/layouts/e_admin.html").read_text(encoding="utf8")
    assert 'href="/showcases/reference"' not in layout and "<span>本人資訊</span>" not in layout
    assert "{% set sidebar_workspace = true %}" in layout


def test_empty_groups_are_hidden_and_unknown_and_system_entries_go_last() -> None:
    entries = [
        _entry("sys.accounts", "帳號管理"),
        _entry("sys.department-permissions", "部門功能設定"),
        _entry("x1", "其他一"),
        _entry("inv.upload", "發票上傳"),
        _entry("sys.departments", "部門管理"),
    ]
    groups = group_sidebar(entries)
    assert [(g.key, [e.code for e in g.entries]) for g in groups] == [
        ("invoice", ["inv.upload"]),
        ("other", ["x1"]),
        ("system", ["sys.departments", "sys.accounts"]),
    ]


def test_no_entries_means_no_groups() -> None:
    assert group_sidebar([]) == []


@pytest.mark.parametrize(
    ("route", "path", "active", "expected"),
    [
        ("/invoices", "/invoices", None, True),
        ("/invoices", "/invoices/abc", None, True),
        ("/invoices", "/invoices-x", None, False),
        ("/invoices", "/invoices/upload", "/invoices/upload", False),
        ("/invoices/upload", "/invoices/upload", "/invoices/upload", True),
        (None, "/invoices", None, False),
    ],
)
def test_is_active_route_matches_the_previous_sidebar_rule(
    route: str | None, path: str, active: str | None, expected: bool
) -> None:
    assert is_active_route(route, path, active) is expected


def test_group_partial_has_accordion_and_collapse_markup() -> None:
    groups = (ROOT / "app/templates/partials/sidebar_groups.html").read_text(encoding="utf8")
    head = (ROOT / "app/templates/partials/sidebar_head.html").read_text(encoding="utf8")
    assert (
        "aria-expanded" in groups and "aria-controls" in groups and "data-sidebar-group" in groups
    )
    assert 'include "partials/sidebar_logout.html"' in groups
    assert "data-sidebar-collapse" in head and 'aria-label="收合側欄"' in head
    assert "/welcome" not in groups


def test_main_js_keeps_sidebar_preferences_optional() -> None:
    script = (ROOT / "app/static/js/main.js").read_text(encoding="utf8")
    assert "ig-admin-sidebar-collapsed" in script and "ig-admin-sidebar-open-groups" in script
    # 讀寫瀏覽器儲存都包在 try／catch：無痕或封鎖時照預設顯示
    assert script.count("window.localStorage") == 2
    assert script.count("} catch {") >= 2


def test_login_form_does_not_show_the_busy_overlay() -> None:
    login = (ROOT / "app/templates/pages/auth/login.html").read_text(encoding="utf8")
    assert 'action="/login" class="e-login__form" data-busy=""' in login
