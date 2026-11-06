# ruff: noqa: F811
"""測試目標：打開發票清單（或儀表板）不得刪除任何使用者尚未提交的草稿。

背景：草稿只存在伺服器程序的記憶體，所有使用者共用同一個字典；清單頁與儀表板以前一進入就 `.clear()`，
乙打開清單，甲正在核對的草稿就消失（核對與提交都回 404）。
規則：
    - 未提交的草稿（不分是誰的）一律不動；
    - 只有「目前使用者自己、已提交」的草稿（沒有影像、案件已在資料庫）在清單頁順便清掉；別人已提交的草稿不動；
    - 重新上傳（discard）仍只能刪本人指定的未提交草稿。
記憶體內、假 API、假部門模組的離線測試，不是正式環境驗證。
"""

import copy
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from test_intake_routes import SUBMITTED, FakeApi, api, review_data  # noqa: F401

# 沿用流程測試的假部門模組與工具（同一個 app、真正的登入與權限檢查）
from test_invoice_flow_intake import (  # noqa: F401, I001
    _ORIGIN,
    ME,
    OTHER,
    _discard,
    _start_draft,
    _upload,
    browser_as,
)

from app.routers import invoices as invoices_router


@pytest.fixture(autouse=True)
def _clean_draft_store() -> Iterator[None]:
    """草稿字典是模組層級的記憶體；每個測試前後清空，避免前一個測試留下的草稿影響下一個。"""
    invoices_router._DRAFT_INVOICE_STORE.clear()
    yield
    invoices_router._DRAFT_INVOICE_STORE.clear()


LIST_PAGES = [
    ("/invoices", "採購部"),
    ("/invoices/review", "財務部"),
    ("/invoices/voucher", "財務部"),
]


def _content(draft: dict[str, Any]) -> dict[str, Any]:
    return copy.copy(draft)


@pytest.mark.parametrize(("path", "department"), LIST_PAGES)
def test_another_users_list_page_never_deletes_my_unsubmitted_draft(
    browser_as: Any, api: FakeApi, path: str, department: str
) -> None:
    mine, _ = browser_as(ME)
    draft_id = _start_draft(mine)
    _upload(mine, draft_id)
    held = invoices_router._DRAFT_INVOICE_STORE[draft_id]
    before = _content(held)

    other, _ = browser_as(OTHER, department)
    page = other.get(path)

    assert page.status_code == 200
    assert invoices_router._DRAFT_INVOICE_STORE.get(draft_id) is held  # 同一份物件，沒被刪
    assert _content(held) == before and held.get("intake_form") and held.get("file_bytes")
    # 甲仍然可以檢核、也可以提交（草稿真的還能用）
    mine, _ = browser_as(ME)
    calls = len(api.requests)
    check = mine.post(
        f"/invoices/{draft_id}/flow/check", headers=_ORIGIN, data=review_data("check")
    )
    assert check.status_code == 200 and len(api.requests) > calls
    api.routes["/api/v1/invoices"] = httpx.Response(201, json=SUBMITTED)
    submit = mine.post(
        f"/invoices/{draft_id}/flow/review", headers=_ORIGIN, data=review_data("submit")
    )
    assert submit.status_code == 303 and submit.headers["location"].endswith("step=4")


def test_my_own_list_page_keeps_my_unsubmitted_draft(browser_as: Any, api: FakeApi) -> None:
    mine, _ = browser_as(ME)
    draft_id = _start_draft(mine)
    _upload(mine, draft_id)
    held = invoices_router._DRAFT_INVOICE_STORE[draft_id]

    assert mine.get("/invoices").status_code == 200

    assert invoices_router._DRAFT_INVOICE_STORE.get(draft_id) is held
    assert held.get("intake_form") is not None and held.get("file_bytes")


def test_list_page_only_cleans_my_own_submitted_drafts(browser_as: Any, api: FakeApi) -> None:
    mine, _ = browser_as(ME)
    my_submitted = _start_draft(mine)
    _upload(mine, my_submitted)
    api.routes["/api/v1/invoices"] = httpx.Response(201, json=SUBMITTED)
    mine.post(f"/invoices/{my_submitted}/flow/review", headers=_ORIGIN, data=review_data("submit"))
    assert invoices_router._DRAFT_INVOICE_STORE[my_submitted].get("submitted_invoice_id")
    my_open = _start_draft(mine)
    _upload(mine, my_open)

    other, _ = browser_as(OTHER)
    other_open = _start_draft(other)
    _upload(other, other_open)
    other_done = _start_draft(other)
    _upload(other, other_done)
    other.post(f"/invoices/{other_done}/flow/review", headers=_ORIGIN, data=review_data("submit"))
    assert invoices_router._DRAFT_INVOICE_STORE[other_done].get("submitted_invoice_id")

    mine, _ = browser_as(ME)
    assert mine.get("/invoices").status_code == 200

    store = invoices_router._DRAFT_INVOICE_STORE
    assert my_submitted not in store  # 自己已提交的草稿（沒有影像、案件在資料庫）順便清掉
    assert my_open in store and other_open in store  # 任何人的未提交草稿都在
    assert other_done in store  # 別人已提交的草稿不動


def test_list_page_changes_nothing_else_and_shows_no_draft(browser_as: Any, api: FakeApi) -> None:
    mine, _ = browser_as(ME)
    draft_id = _start_draft(mine)
    _upload(mine, draft_id)
    cases_before = copy.deepcopy(invoices_router._INVOICE_STORE)
    drafts_before = set(invoices_router._DRAFT_INVOICE_STORE)
    calls = len(api.requests)

    page = mine.get("/invoices")

    assert page.status_code == 200 and draft_id not in page.text  # 清單不列出草稿，也不洩漏 id
    assert cases_before == invoices_router._INVOICE_STORE
    assert set(invoices_router._DRAFT_INVOICE_STORE) == drafts_before  # 沒有新增或刪除草稿
    assert len(api.requests) == calls  # 沒有任何 API 呼叫


def test_discard_still_only_deletes_my_own_unsubmitted_draft_after_listing(
    browser_as: Any, api: FakeApi
) -> None:
    mine, _ = browser_as(ME)
    mine_id = _start_draft(mine)
    _upload(mine, mine_id)
    other, _ = browser_as(OTHER)
    other_id = _start_draft(other)
    _upload(other, other_id)
    other.get("/invoices")
    mine, _ = browser_as(ME)
    mine.get("/invoices")

    assert _discard(mine, other_id).status_code == 403  # 不能刪別人的
    assert other_id in invoices_router._DRAFT_INVOICE_STORE
    assert _discard(mine, mine_id).status_code == 303  # 本人指定的未提交草稿照常刪除
    assert mine_id not in invoices_router._DRAFT_INVOICE_STORE
    assert other_id in invoices_router._DRAFT_INVOICE_STORE


def test_dashboard_does_not_delete_drafts(admin_client: TestClient) -> None:
    new = admin_client.get("/invoices/new")
    assert new.status_code == 303
    draft_id = new.headers["location"].split("/")[2]
    held = invoices_router._DRAFT_INVOICE_STORE[draft_id]

    assert admin_client.get("/dashboard").status_code == 200

    assert invoices_router._DRAFT_INVOICE_STORE.get(draft_id) is held
