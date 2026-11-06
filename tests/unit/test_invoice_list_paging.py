"""測試目標：發票列表每頁 30 筆、序號、上傳日期、含稅總額與品名（2026-11-06）。全部離線，不連資料庫。"""

from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db.repositories import invoice_repository as repo
from app.routers import invoices as inv
from app.utils.list_paging import PAGE_SIZE, page_links, page_url, paginate

# ---- 分頁工具 ----


def test_page_size_is_thirty() -> None:
    assert PAGE_SIZE == 30


@pytest.mark.parametrize(
    ("total", "page", "expected"),
    [
        (0, 1, (1, 1, 0, 0)),
        (25, 1, (1, 1, 1, 25)),
        (65, 2, (2, 3, 31, 60)),
        (65, 3, (3, 3, 61, 65)),
        (65, 99, (3, 3, 61, 65)),  # 超過最後一頁時夾回最後一頁
        (65, 0, (1, 3, 1, 30)),
    ],
)
def test_paginate_slices_and_clamps(total: int, page: int, expected: tuple[int, ...]) -> None:
    view = paginate(list(range(total)), page)
    assert (view.page, view.pages, view.start, view.end) == expected
    assert len(view.items) == (view.end - view.start + 1 if view.start else 0)


@pytest.mark.parametrize(
    ("page", "pages", "expected"),
    [
        (1, 1, (1,)),
        (2, 3, (1, 2, 3)),
        (7, 14, (1, None, 6, 7, 8, None, 14)),
        (1, 14, (1, 2, None, 14)),
        (14, 14, (1, None, 13, 14)),
    ],
)
def test_page_links_keep_ends_and_neighbours(
    page: int, pages: int, expected: tuple[int | None, ...]
) -> None:
    assert page_links(page, pages) == expected


def test_page_url_keeps_filters_and_omits_first_page() -> None:
    assert page_url("/invoices", 1) == "/invoices"
    assert (
        page_url("/invoices", 2, q="影印紙", status=None)
        == "/invoices?q=%E5%BD%B1%E5%8D%B0%E7%B4%99&page=2"
    )
    assert (
        page_url("/invoices/review", 3, status="returned")
        == "/invoices/review?status=returned&page=3"
    )


# ---- 列表頁 ----


def _many_invoices(count: int) -> list[dict[str, Any]]:
    base = deepcopy(inv._INVOICE_STORE[0])
    rows = []
    for n in range(count):
        row = deepcopy(base)
        row.update(
            id=10_000 + n,
            invoice_number=f"ZZ{n:08d}",
            item_description=f"品名{n}",
            total_amount=1_050 + n,
            uploaded_date="115/11/06",
        )
        rows.append(row)
    return rows


@pytest.fixture
def sixty_five(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows = _many_invoices(65)
    monkeypatch.setattr(inv, "_get_active_invoices", lambda request=None: rows)
    return rows


def test_first_page_shows_thirty_rows_with_serial_numbers(
    admin_client: TestClient, sixty_five: list[dict[str, Any]]
) -> None:
    html = admin_client.get("/invoices").text
    assert html.count('<td class="col-no">') == 30
    assert '<td class="col-no">1</td>' in html and '<td class="col-no">30</td>' in html
    assert "第 1–30 筆，共 65 筆" in html
    assert 'href="/invoices?page=2"' in html


def test_second_page_continues_the_serial_numbers(
    admin_client: TestClient, sixty_five: list[dict[str, Any]]
) -> None:
    html = admin_client.get("/invoices?page=2").text
    assert '<td class="col-no">31</td>' in html and '<td class="col-no">60</td>' in html
    assert '<td class="col-no">1</td>' not in html
    assert "第 31–60 筆，共 65 筆" in html
    assert 'aria-current="page">2<' in html


def test_page_links_keep_the_search_keyword(
    admin_client: TestClient, sixty_five: list[dict[str, Any]]
) -> None:
    html = admin_client.get("/invoices?q=%E5%93%81%E5%90%8D").text
    assert "q=%E5%93%81%E5%90%8D&amp;page=2" in html


def test_stat_cards_count_all_invoices_not_just_the_page(
    admin_client: TestClient, sixty_five: list[dict[str, Any]]
) -> None:
    first = admin_client.get("/invoices").text
    last = admin_client.get("/invoices?page=3").text
    cards = first[: first.index("發票清單")]
    assert cards == last[: last.index("發票清單")]


def test_columns_show_upload_date_second_and_total_only(
    admin_client: TestClient, sixty_five: list[dict[str, Any]]
) -> None:
    html = admin_client.get("/invoices").text
    head = html[html.index("<thead>") : html.index("</thead>")]
    order = [
        head.index(label) for label in ("序號", "上傳日期", "發票號碼", "品名項目", "含稅總額")
    ]
    assert order == sorted(order)
    assert "未稅金額" not in head and ">稅額<" not in head
    assert "115/11/06" in html and "品名0" in html and "1,050" in html


def test_small_list_shows_count_without_page_links(admin_client: TestClient) -> None:
    html = admin_client.get("/invoices").text
    assert "共 " in html and "pagination__num" not in html


# ---- 資料庫列表：品名與上傳日期 ----


def test_list_sql_reads_item_names_and_searches_them() -> None:
    sql = str(repo.SELECT_INVOICES)
    assert "string_agg(item_name, '、' ORDER BY line_no)" in sql
    assert "COALESCE(it.item_names, '') ILIKE" in sql


def test_row_uses_item_names_and_taipei_upload_date() -> None:
    row = {
        "invoice_id": "44444444-4444-4444-8444-444444444444",
        "status": "pending_accounting_review",
        "invoice_track": "QU",
        "invoice_number": "08904069",
        "item_names": "影印紙、碳粉匣",
        # UTC 11/05 17:00 ＝ 台灣 11/06 01:00
        "created_at": datetime(2026, 11, 5, 17, 0, tzinfo=UTC),
        "invoice_date": date(2025, 10, 3),
    }
    result = repo._row_to_invoice_dict(row)
    assert result["item_description"] == "影印紙、碳粉匣"
    assert result["uploaded_date"] == "115/11/06"
    assert result["invoice_date"] == "114/10/03"


def test_row_without_items_or_created_time_stays_blank() -> None:
    result = repo._row_to_invoice_dict({"invoice_id": "x", "status": "draft"})
    assert result["item_description"] == "" and result["uploaded_date"] == ""
