"""測試目標：側欄依使用者核定順序顯示（2026-11-05）；只重排，不新增或移除項目。全部離線。"""

from pathlib import Path

from app.core.templating import templates
from app.schemas.e_access import SidebarEntry
from app.utils.sidebar_order import order_sidebar

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
        assert "|sidebar_order" in (ROOT / path).read_text(encoding="utf8")


def test_login_form_does_not_show_the_busy_overlay() -> None:
    login = (ROOT / "app/templates/pages/auth/login.html").read_text(encoding="utf8")
    assert 'action="/login" class="e-login__form" data-busy=""' in login
