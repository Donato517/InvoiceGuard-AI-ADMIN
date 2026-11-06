"""
模組名稱：tests.unit.test_self_upload_flow_gates
功能說明：本人上傳的發票不得查看步驟 5、6，也不得從步驟 4 往後推進；步驟 4 改顯示「檢視我的發票」。
相依模組：app.routers.invoices
注意事項：
    - 2026-11-06 使用者確認的正式需求，取代先前「本人可進入步驟 5 檢視、只是不能操作」的設計。
    - 步驟 7～9 開放本人作業，但須先由具財務部權限的他人核准（且已推進到步驟 7）；2026-11-06 使用者確認。
    - 核准、退回、儲存會計欄位與舊 /review 的本人限制由 test_finance_self_upload 涵蓋。
    - 只用合成 UUID 與示範資料，不連資料庫；離線測試，不是正式環境驗收。
"""

from collections.abc import Iterator
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.routers import invoices as invoices_router

ME = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
STRANGER = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def _restore_demo_state() -> Iterator[None]:
    store = deepcopy(invoices_router._INVOICE_STORE)
    yield
    invoices_router._INVOICE_STORE[:] = store
    invoices_router._FINANCE_CASES.clear()
    invoices_router._FINANCE_HISTORY.clear()
    invoices_router._RETURN_SNAPSHOTS.clear()


def _login(user_id: str) -> None:
    from app.core.deps import require_admin
    from app.main import app

    def as_user(request: Request) -> SimpleNamespace:
        admin = SimpleNamespace(id=user_id, display_name="測試人員", role="admin", is_active=True)
        request.state.admin = admin
        return admin

    app.dependency_overrides[require_admin] = as_user


@pytest.fixture
def finance_me(admin_client: TestClient) -> TestClient:
    _login(ME)
    return admin_client


def _invoice(invoice_id: int = 3, *, uploader: str, stage: int = 5) -> dict:
    invoice = invoices_router._find_invoice(str(invoice_id))
    invoice["uploaded_by"] = uploader
    invoice["stage"] = stage
    invoice["is_completed"] = False
    return invoice


# ---- 本人：不得查看步驟 5、6 ----
@pytest.mark.parametrize("step", [5, 6])
def test_self_upload_cannot_view_review_and_filing_steps(finance_me: TestClient, step: int) -> None:
    _invoice(uploader=ME, stage=9)
    assert finance_me.get(f"/invoices/3/flow?step={step}").status_code == 403


def test_self_upload_can_still_view_steps_up_to_four(finance_me: TestClient) -> None:
    _invoice(uploader=ME, stage=4)
    for step in (3, 4):
        assert finance_me.get(f"/invoices/3/flow?step={step}").status_code == 200


# ---- 本人：不得從步驟 4 進入步驟 5，也不得在步驟 5、6 推進 ----
@pytest.mark.parametrize("from_step", [4, 5, 6])
def test_self_upload_cannot_advance_into_or_through_review(
    finance_me: TestClient, from_step: int
) -> None:
    invoice = _invoice(uploader=ME, stage=from_step)
    response = finance_me.post("/invoices/3/flow/advance", data={"from_step": from_step})
    assert response.status_code == 403
    assert invoice["stage"] == from_step
    assert invoice["review_status"] == "pending" and invoice["status"] == "flagged"


# ---- 本人：步驟 7～9 須待他人（具財務權限）核准後才開放 ----
def _approve_by_other(invoice: dict, stage: int = 7) -> None:
    invoice["review_status"], invoice["status"] = "approved", "passed"
    invoice["stage"] = stage


@pytest.mark.parametrize("step", [7, 8, 9])
def test_self_upload_cannot_open_late_steps_before_other_approves(
    finance_me: TestClient, step: int
) -> None:
    _invoice(uploader=ME, stage=9)  # 階段再高，只要尚未核准就不放行
    assert finance_me.get(f"/invoices/3/flow?step={step}").status_code == 403


def test_self_upload_cannot_advance_or_complete_before_other_approves(
    finance_me: TestClient,
) -> None:
    invoice = _invoice(uploader=ME, stage=7)
    assert finance_me.post("/invoices/3/flow/advance", data={"from_step": 7}).status_code == 403
    assert finance_me.post("/invoices/3/flow/complete").status_code == 403
    assert invoice["stage"] == 7 and invoice["is_completed"] is False


def test_approved_but_not_yet_advanced_to_step_7_stays_closed(finance_me: TestClient) -> None:
    """已核准但仍停在步驟 5、6（尚未由財務推進）時，本人不能跳過去。"""
    invoice = _invoice(uploader=ME, stage=6)
    _approve_by_other(invoice, stage=6)
    assert finance_me.get("/invoices/3/flow?step=7").status_code == 403


@pytest.mark.parametrize("step", [7, 8, 9])
def test_self_upload_can_work_late_steps_after_other_approves(
    finance_me: TestClient, step: int
) -> None:
    _approve_by_other(_invoice(uploader=ME, stage=7))
    assert finance_me.get(f"/invoices/3/flow?step={step}").status_code == 200


def test_self_upload_advance_and_complete_after_other_approves(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=ME, stage=7)
    _approve_by_other(invoice)
    assert finance_me.post("/invoices/3/flow/advance", data={"from_step": 7}).status_code == 303
    assert finance_me.post("/invoices/3/flow/complete").status_code == 303
    assert invoice["is_completed"] is True


def test_steps_5_and_6_stay_closed_even_after_approval(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=ME, stage=9)
    _approve_by_other(invoice, stage=9)
    for step in (5, 6):
        assert finance_me.get(f"/invoices/3/flow?step={step}").status_code == 403
    assert finance_me.post("/invoices/3/flow/advance", data={"from_step": 6}).status_code == 403


def test_resubmit_resets_approval_and_closes_late_steps_again(finance_me: TestClient) -> None:
    """補正重送會把審核狀態重置為待審，本人須重新等待他人核准。"""
    invoice = _invoice(uploader=ME, stage=7)
    _approve_by_other(invoice)
    assert finance_me.get("/invoices/3/flow?step=7").status_code == 200
    invoice["review_status"], invoice["stage"] = "pending", 4
    assert finance_me.get("/invoices/3/flow?step=7").status_code == 403


def test_other_users_late_steps_are_not_affected(finance_me: TestClient) -> None:
    _invoice(uploader=OTHER, stage=9)
    for step in (5, 6, 7, 8, 9):
        assert finance_me.get(f"/invoices/3/flow?step={step}").status_code == 200


def test_step_nav_links_follow_the_late_step_rule(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=ME, stage=9)
    html = finance_me.get("/invoices/3/flow?step=4").text
    assert "/flow?step=7" not in html
    _approve_by_other(invoice, stage=9)
    html = finance_me.get("/invoices/3/flow?step=4").text
    assert "/flow?step=7" in html and "/flow?step=5" not in html


def test_list_link_for_self_upload_follows_the_late_step_rule(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=ME, stage=7)
    html = finance_me.get("/invoices").text
    assert "/invoices/3/flow?step=7" not in html
    _approve_by_other(invoice)
    html = finance_me.get("/invoices").text
    assert "/invoices/3/flow?step=7" in html


# ---- 駁回／作廢／僅標記完成：不得因 is_completed 放行 ----
@pytest.mark.parametrize("review_status", ["rejected", "voided", "pending", "returned"])
def test_is_completed_without_approval_never_opens_late_steps(
    finance_me: TestClient, review_status: str
) -> None:
    invoice = _invoice(uploader=ME, stage=9)
    invoice["is_completed"], invoice["review_status"] = True, review_status
    for step in (7, 8, 9):
        assert finance_me.get(f"/invoices/3/flow?step={step}").status_code == 403
    assert finance_me.post("/invoices/3/flow/advance", data={"from_step": 7}).status_code == 403
    assert finance_me.post("/invoices/3/flow/complete").status_code == 403
    assert finance_me.post("/invoices/3/import-confirm").status_code == 403
    assert finance_me.get("/invoices/3/export/report").status_code == 403


# ---- 資料庫案件與記憶體示範案件的狀態映射（離線；不連資料庫）----
@pytest.mark.parametrize(
    ("db_status", "allowed"),
    [
        ("draft", False),
        ("pending_unit_approval", False),
        ("pending_accounting_review", False),
        ("returned_to_applicant", False),
        ("returned_to_accounting", False),
        ("rejected", False),
        ("voided", False),
        ("approved", True),
    ],
)
def test_db_status_mapping_decides_late_step_access(db_status: str, allowed: bool) -> None:
    from app.db.repositories.invoice_repository import _row_to_invoice_dict

    row = {"invoice_id": "44444444-4444-4444-8444-444444444444", "status": db_status}
    mapped = _row_to_invoice_dict(row)
    assert invoices_router._other_finance_approved(mapped) is allowed
    if db_status == "approved":  # 資料庫核准後直接映射到步驟 9，沒有獨立的步驟 7 推進紀錄
        assert mapped["stage"] == 9


def test_demo_case_needs_both_approval_and_stage_seven() -> None:
    f = invoices_router._other_finance_approved
    assert f({"review_status": "approved", "stage": 6, "is_completed": False}) is False
    assert f({"review_status": "approved", "stage": 7, "is_completed": False}) is True
    assert f({"review_status": "pending", "stage": 9, "is_completed": True}) is False


# ---- 獨立路由：單張申報、匯入確認、單張匯出 ----
def _mutation_marks(invoice: dict) -> tuple[bool, bool]:
    return "filing_preview" in invoice, "import_confirmed_at" in invoice


def test_standalone_routes_forbidden_for_self_before_approval(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=ME, stage=4)
    invoice.pop("filing_preview", None)
    invoice.pop("import_confirmed_at", None)
    assert finance_me.post("/invoices/3/filing").status_code == 403
    assert finance_me.post("/invoices/3/import-confirm").status_code == 403
    for kind in ("report", "input", "voucher"):
        assert finance_me.get(f"/invoices/3/export/{kind}").status_code == 403
    assert _mutation_marks(invoice) == (False, False)


def test_standalone_routes_after_approval_for_self(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=ME, stage=7)
    _approve_by_other(invoice)
    invoice.pop("filing_preview", None)
    # 步驟 6 的單張申報產生，本人核准後仍一律禁止
    assert finance_me.post("/invoices/3/filing").status_code == 403
    assert "filing_preview" not in invoice
    assert finance_me.post("/invoices/3/import-confirm").status_code == 303
    assert "import_confirmed_at" in invoice
    assert finance_me.get("/invoices/3/export/report").status_code == 200
    assert finance_me.get("/invoices/3/export/input").status_code == 200


def test_standalone_routes_unchanged_for_other_finance_user(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=OTHER, stage=4)
    invoice.pop("filing_preview", None)
    assert finance_me.post("/invoices/3/filing").status_code == 303
    assert finance_me.post("/invoices/3/import-confirm").status_code == 303
    assert finance_me.get("/invoices/3/export/report").status_code == 200


# ---- 批次申報產檔：本人未核准的發票不得納入 ----
class _FakeBatchResult:
    content = "FAKE"
    filename = "12345678.TXT"
    issues: dict = {}


@pytest.fixture
def batch_builder(monkeypatch: pytest.MonkeyPatch) -> list[list[dict]]:
    calls: list[list[dict]] = []

    def fake(snapshots, profile, period):  # noqa: ANN001
        calls.append(list(snapshots))
        return _FakeBatchResult()

    monkeypatch.setattr(invoices_router, "build_format21_file", fake)
    return calls


def _batch(client: TestClient):
    return client.post("/invoices/filing/export", data={"period": "11310", "invoice_ids": ["3"]})


def test_batch_export_refuses_unapproved_self_invoice(
    finance_me: TestClient, batch_builder: list
) -> None:
    invoice = _invoice(uploader=ME, stage=9)
    # 資料庫「待審」案件的檢視狀態是 passed，F09 會誤判為已核准，所以路由必須先擋
    invoice["review_status"], invoice["status"] = "pending", "passed"
    response = _batch(finance_me)
    assert response.status_code == 403
    assert batch_builder == []  # 沒有呼叫產檔，也就沒有檔案


def test_batch_export_allows_approved_self_invoice(
    finance_me: TestClient, batch_builder: list
) -> None:
    _approve_by_other(_invoice(uploader=ME, stage=7))
    response = _batch(finance_me)
    assert response.status_code == 200 and response.content == b"FAKE"
    assert len(batch_builder) == 1  # 仍由原有 F09 檢核函式把關（此處以替身代替）


def test_batch_export_unchanged_for_other_finance_user(
    finance_me: TestClient, batch_builder: list
) -> None:
    invoice = _invoice(uploader=OTHER, stage=4)
    invoice["review_status"], invoice["status"] = "pending", "passed"
    assert _batch(finance_me).status_code == 200
    assert len(batch_builder) == 1


def test_batch_export_mixed_selection_is_refused_as_a_whole(
    finance_me: TestClient, batch_builder: list
) -> None:
    _invoice(3, uploader=ME, stage=4)
    _invoice(4, uploader=OTHER, stage=7)
    response = finance_me.post(
        "/invoices/filing/export", data={"period": "11310", "invoice_ids": ["3", "4"]}
    )
    assert response.status_code == 403 and batch_builder == []


def test_self_review_post_error_does_not_render_review_step(finance_me: TestClient) -> None:
    """直接 POST 核准被拒時，回應不得再呈現步驟 5 的審核內容。"""
    _invoice(uploader=ME, stage=5)
    response = finance_me.post("/invoices/3/finance-review/approve")
    assert response.status_code == 403
    assert "不得由您自行核准、退回或儲存會計欄位" in response.text
    assert "核對欄位（14 項）" not in response.text
    assert 'action="/invoices/3/finance-review/approve"' not in response.text


# ---- 本人：步驟 4 的按鈕與導覽 ----
def _as_non_finance(user_id: str) -> None:
    """非財務部門的使用者（只開放前四步），直接替身路由層的存取依賴。"""
    from app.main import app

    def access(request: Request) -> None:
        request.state.invoice_user_id = user_id
        request.state.is_finance_workspace = False
        request.state.invoice_max_step = 4

    app.dependency_overrides[invoices_router.require_invoice_access] = access


def test_self_upload_step4_shows_view_my_invoice_without_review_entry(
    finance_me: TestClient,
) -> None:
    _invoice(uploader=ME, stage=4)
    html = finance_me.get("/invoices/3/flow?step=4").text
    assert "檢視我的發票" in html and "data-view-my-invoice" in html
    assert "確認並繼續" not in html
    assert 'action="/invoices/3/flow/advance"' not in html
    assert "前往步驟 5 財務審核" not in html
    assert "/flow?step=5" not in html


def test_non_finance_self_upload_step4_shows_the_same_button(admin_client: TestClient) -> None:
    _as_non_finance(ME)
    _invoice(uploader=ME, stage=4)
    html = admin_client.get("/invoices/3/flow?step=4").text
    assert "檢視我的發票" in html and "完成前四步，返回待處理發票" not in html
    assert 'action="/invoices/3/flow/advance"' not in html


def test_view_my_invoice_keeps_first_four_persistence_and_returns_to_list(
    finance_me: TestClient,
) -> None:
    """按鈕仍走 finish-first-four（寫入「前四步已核對」標記），再導回發票管理列表；不進步驟 5。"""
    invoice = _invoice(uploader=ME, stage=4)
    html = finance_me.get("/invoices/3/flow?step=4").text
    assert 'action="/invoices/3/flow/finish-first-four"' in html
    response = finance_me.post("/invoices/3/flow/finish-first-four")
    assert (response.status_code, response.headers["location"]) == (303, "/invoices")
    assert invoice["first_four_checked"] is True and invoice["stage"] == 4
    assert invoice["review_status"] == "pending"


def test_unsubmitted_draft_gets_a_plain_link_not_the_409_endpoint(finance_me: TestClient) -> None:
    """暫存草稿的 finish-first-four 會回 409，所以改用一般連結（不寫入、不進步驟 5）。"""
    draft = deepcopy(_invoice(uploader=ME, stage=4))
    draft["id"] = "5b0f8b0e-6c7a-4c55-9a1e-0a1b2c3d4e5f"
    invoices_router._DRAFT_INVOICE_STORE[draft["id"]] = draft
    try:
        html = finance_me.get(f"/invoices/{draft['id']}/flow?step=4").text
    finally:
        invoices_router._DRAFT_INVOICE_STORE.clear()
    assert 'href="/invoices" class="btn btn--primary" data-view-my-invoice' in html
    assert "finish-first-four" not in html and "/flow/advance" not in html


def test_view_my_invoice_target_is_the_invoice_management_list(finance_me: TestClient) -> None:
    _invoice(uploader=ME, stage=4)
    response = finance_me.get("/invoices")
    assert response.status_code == 200
    assert "發票管理" in response.text  # 完整發票管理列表（財務部可見所有發票）
    assert "/invoices/3" in response.text


def test_self_upload_stepper_does_not_link_to_steps_5_and_6(finance_me: TestClient) -> None:
    _invoice(uploader=ME, stage=9)
    html = finance_me.get("/invoices/3/flow?step=4").text
    assert "/flow?step=5" not in html and "/flow?step=6" not in html


def test_list_link_for_self_upload_avoids_blocked_steps(finance_me: TestClient) -> None:
    _invoice(uploader=ME, stage=6)
    html = finance_me.get("/invoices").text
    assert "/invoices/3/flow?step=4" in html
    assert "/invoices/3/flow?step=5" not in html and "/invoices/3/flow?step=6" not in html


# ---- 其他審核者：照常 ----
def test_other_reviewer_can_view_and_advance_finance_steps(finance_me: TestClient) -> None:
    invoice = _invoice(uploader=OTHER, stage=4)
    assert finance_me.get("/invoices/3/flow?step=5").status_code == 200
    assert finance_me.get("/invoices/3/flow?step=6").status_code == 200
    to_review = finance_me.post("/invoices/3/flow/advance", data={"from_step": 4})
    assert (to_review.status_code, to_review.headers["location"]) == (
        303,
        "/invoices/3/flow?step=5",
    )
    to_filing = finance_me.post("/invoices/3/flow/advance", data={"from_step": 5})
    assert (to_filing.status_code, to_filing.headers["location"]) == (
        303,
        "/invoices/3/flow?step=6",
    )
    assert invoice["stage"] == 6
    assert finance_me.post("/invoices/3/flow/complete").status_code == 303


def test_other_reviewer_step4_keeps_continue_button(finance_me: TestClient) -> None:
    _invoice(uploader=OTHER, stage=4)
    html = finance_me.get("/invoices/3/flow?step=4").text
    assert "確認並繼續" in html and "檢視我的發票" not in html


def test_non_finance_user_still_cannot_reach_others_invoice(admin_client: TestClient) -> None:
    """沒有財務權限的人，不因本次修改取得他人發票或步驟 5、6 的存取權。"""
    _login(STRANGER)
    request_state = {"is_finance_workspace": False, "invoice_max_step": 4}

    def deny_non_finance(request: Request) -> None:
        request.state.invoice_user_id = STRANGER
        request.state.is_finance_workspace = request_state["is_finance_workspace"]
        request.state.invoice_max_step = request_state["invoice_max_step"]

    from app.main import app

    app.dependency_overrides[invoices_router.require_invoice_access] = deny_non_finance
    try:
        _invoice(uploader=OTHER, stage=5)
        assert admin_client.get("/invoices/3/flow?step=4").status_code == 403
        assert admin_client.get("/invoices/3/flow?step=5").status_code == 403
        assert admin_client.get("/invoices/3").status_code == 403
        assert (
            admin_client.post("/invoices/3/flow/advance", data={"from_step": 4}).status_code == 403
        )
    finally:
        app.dependency_overrides.pop(invoices_router.require_invoice_access, None)


# ---- 資料庫發票的 UUID 比對（不只靠示範帳號）----
@pytest.mark.parametrize(
    "uploader",
    [ME, ME.upper(), UUID(ME), f" {ME} "],
    ids=["lower", "upper", "uuid-object", "padded"],
)
def test_is_self_upload_matches_real_uuid_forms(uploader: object) -> None:
    assert invoices_router._is_self_upload(uploader, ME) is True


def test_is_self_upload_ignores_demo_placeholder_and_other_users() -> None:
    assert invoices_router._is_self_upload("1", "1") is False  # 示範整數佔位 ID 不算本人
    assert invoices_router._is_self_upload(OTHER, ME) is False
    assert invoices_router._is_self_upload(None, ME) is False


def test_database_invoice_with_uppercase_uploader_uuid_is_denied(
    finance_me: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """資料庫來源的發票：uploaded_by 取自 submitted_by（文字 UUID），大小寫不同仍視為本人。"""
    db_id = "5b0f8b0e-6c7a-4c55-9a1e-0a1b2c3d4e5f"
    row = deepcopy(invoices_router.DEMO_TEST_INVOICES[2])
    row.update({"id": db_id, "uploaded_by": ME.upper(), "stage": 5})
    monkeypatch.setattr(
        invoices_router.invoice_review_service, "fetch_invoice", lambda invoice_id: row
    )
    for step in (5, 6):
        assert finance_me.get(f"/invoices/{db_id}/flow?step={step}").status_code == 403
    assert (
        finance_me.post(f"/invoices/{db_id}/flow/advance", data={"from_step": 4}).status_code == 403
    )
