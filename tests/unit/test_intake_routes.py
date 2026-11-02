"""測試目標：W2／W3——/intake 上傳、核對、檢核、提交路由；權限雙重關閉；錯誤與結果不明的處理。

這是替身測試：假 EModule（登入與權限）、假 API 傳輸（httpx.MockTransport）、假的查證函式；
不連 DB、Storage 或真實 API，**不代表已與真實 API 串接**。
"""

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from app.constants import SESSION_COOKIE_NAME
from app.integrations.api_intake_client import ApiIntakeClient
from app.routers import intake as intake_router
from app.schemas.e_access import EffectiveDepartment
from app.schemas.e_admin import Action, DeniedError, UnauthenticatedError
from app.schemas.intake import OutboundRequest
from app.services import invoice_intake_service as intake_service
from app.services.e_admin_service import get_e_module
from app.services.e_registry import capability

USER = UUID("00000000-0000-4000-8000-000000000001")
DEPT = UUID("00000000-0000-4000-8000-0000000000d1")
RID = "00000000-0000-4000-8000-0000000000a1"
INV = UUID("00000000-0000-4000-8000-0000000000b2")
ORIGIN = {"Origin": "http://testserver"}
RECOGNITION = {
    "recognition_id": RID,
    "result_status": "partial",
    "is_triplicate": True,
    "invoice": {
        "invoice_track": "AB",
        "invoice_number": "12345678",
        "invoice_date": "2026-10-15",
        "seller_tax_id": "04595257",
        "seller_name": "ABC公司",
        "buyer_tax_id": None,
        "buyer_name": "範例科技",
        "tax_type": "taxable",
        "sales_amount": "3000",
        "tax_amount": "150",
        "total_amount": "3150",
        "total_amount_zh": None,
        "items": [
            {
                "line_no": 1,
                "item_name": "辦公文具",
                "quantity": "2",
                "unit_price": "1000",
                "line_amount": "2000",
            },
            {
                "line_no": 2,
                "item_name": "影印紙",
                "quantity": "1",
                "unit_price": "1000",
                "line_amount": "1000",
            },
        ],
    },
    "printed": {},
    "warnings": [{"field": "buyer_tax_id", "code": "x", "message": "買受人統編未辨識，請輸入"}],
    "recognition": {},
    "expires_at": "2026-11-02T12:00:00Z",
}
SUBMITTED = {
    "invoice_id": str(INV),
    "status": "pending_accounting_review",
    "tax_period": "202610",
    "submitted_at": "2026-11-02T01:00:00Z",
    "warnings": [],
    "modified_fields": [],
}


class FakeModule:
    def __init__(self) -> None:
        self.allowed = True
        self.checks: list[tuple[str, Action, Any]] = []

    def identity(self, token: Any) -> SimpleNamespace:
        if token is None:
            raise UnauthenticatedError
        return SimpleNamespace(user_id=USER, selected_department_id=DEPT)

    def check(self, identity: Any, code: str, action: Action, target: Any) -> None:
        self.checks.append((code, action, target.department_id))
        if not self.allowed or code != intake_router.INTAKE:
            raise DeniedError

    def departments(self, identity: Any) -> tuple[EffectiveDepartment, ...]:
        return (EffectiveDepartment(DEPT, "t", "測試部"),)

    def sidebar_menu(self, identity: Any) -> tuple[()]:
        return ()


class FakeApi:
    """依路徑回應的假 API；同時是憑證提供者，記錄每次請求。"""

    def __init__(self) -> None:
        self.routes: dict[str, httpx.Response | Exception] = {}
        self.requests: list[httpx.Request] = []

    def headers(self, request: OutboundRequest) -> dict[str, str]:
        return {"Authorization": "Bearer synthetic-test-credential"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.routes[request.url.path]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def client(self) -> ApiIntakeClient:
        return ApiIntakeClient(
            "https://api.example.test", self, transport=httpx.MockTransport(self.handler)
        )

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def last_json(self, path: str) -> dict[str, Any]:
        return json.loads(next(r for r in reversed(self.requests) if r.url.path == path).content)


@pytest.fixture
def module() -> FakeModule:
    return FakeModule()


@pytest.fixture
def api() -> FakeApi:
    fake = FakeApi()
    fake.routes = {
        "/api/v1/invoices/recognize": httpx.Response(200, json=RECOGNITION),
        "/api/v1/invoices/check-required": httpx.Response(
            200, json={"required_complete": True, "missing": []}
        ),
        "/api/v1/invoices/check-warnings": httpx.Response(
            200, json={"warnings": [], "checked_on": "2026-11-02"}
        ),
        "/api/v1/invoices/check-duplicate": httpx.Response(200, json={"is_duplicate": False}),
        "/api/v1/invoices": httpx.Response(201, json=SUBMITTED),
    }
    return fake


@pytest.fixture
def web(module: FakeModule, api: FakeApi) -> Iterator[TestClient]:
    from app.main import app

    app.dependency_overrides[get_e_module] = lambda: module
    app.dependency_overrides[intake_router.get_intake_client] = api.client
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(SESSION_COOKIE_NAME, "synthetic-session")
    yield client
    app.dependency_overrides.clear()


def review_data(action: str = "check", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "action": action,
        "recognition_id": RID,
        "invoice_track": "AB",
        "invoice_number": "12345678",
        "invoice_date": "2026-10-15",
        "seller_tax_id": "04595257",
        "seller_name": "ABC公司",
        "buyer_tax_id": "",
        "buyer_name": "範例科技",
        "tax_type": "taxable",
        "sales_amount": "3000",
        "tax_amount": "150",
        "total_amount": "3150",
        "total_amount_zh": "",
        "item_source_line_no": ["1", "2"],
        "item_item_name": ["辦公文具", "影印紙"],
        "item_quantity": ["2", "1"],
        "item_unit_price": ["1000", "1000"],
        "item_line_amount": ["2000", "1000"],
    }
    return {**data, **overrides}


# ---- 權限：雙重關閉 ----


def test_unregistered_capability_is_denied_even_for_a_fully_permitted_user() -> None:
    from app.services.e_admin_service import get_e_module as real

    with pytest.raises(DeniedError):
        capability(intake_router.INTAKE, real().registry)  # 正式功能目錄沒有 inv.intake


def test_without_function_permission_everything_is_403(web: TestClient, module: FakeModule) -> None:
    module.allowed = False

    assert web.get("/intake").status_code == 403
    assert (
        web.post(
            "/intake/recognize", headers=ORIGIN, files={"file": ("a.jpg", b"x", "image/jpeg")}
        ).status_code
        == 403
    )
    assert web.post("/intake/review", headers=ORIGIN, data=review_data()).status_code == 403
    assert module.checks and all(
        c[0] == intake_router.INTAKE and c[1] == Action.CREATE for c in module.checks
    )


def test_unauthenticated_is_rejected(module: FakeModule, api: FakeApi) -> None:
    from app.main import app

    app.dependency_overrides[get_e_module] = lambda: module
    try:
        client = TestClient(app, follow_redirects=False)
        assert client.get("/intake").status_code in (303, 401)
        assert client.post("/intake/review", headers=ORIGIN, data=review_data()).status_code in (
            303,
            401,
        )
    finally:
        app.dependency_overrides.clear()
    assert api.requests == []


def test_credentials_not_configured_means_503_and_no_request(module: FakeModule) -> None:
    from app.main import app

    app.dependency_overrides[get_e_module] = lambda: module  # 不覆寫用戶端＝正式預設
    try:
        client = TestClient(app, follow_redirects=False)
        client.cookies.set(SESSION_COOKIE_NAME, "synthetic-session")
        assert client.get("/intake").status_code == 200  # 頁面可開，但任何動作都不行
        files = {"file": ("a.jpg", b"\xff\xd8", "image/jpeg")}
        assert client.post("/intake/recognize", headers=ORIGIN, files=files).status_code == 503
        assert (
            client.post("/intake/review", headers=ORIGIN, data=review_data("submit")).status_code
            == 503
        )
    finally:
        app.dependency_overrides.clear()


def test_posts_require_same_origin(web: TestClient, api: FakeApi) -> None:
    files = {"file": ("a.jpg", b"x", "image/jpeg")}

    assert web.post("/intake/recognize", files=files).status_code == 403
    assert web.post("/intake/review", data=review_data()).status_code == 403
    assert (
        web.post(
            "/intake/review", data=review_data(), headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert api.requests == []


# ---- 上傳與辨識 ----


def test_upload_page(web: TestClient) -> None:
    response = web.get("/intake")

    assert response.status_code == 200 and "開始辨識" in response.text


def test_recognize_renders_the_review_form(web: TestClient, api: FakeApi) -> None:
    response = web.post(
        "/intake/recognize", headers=ORIGIN, files={"file": ("a.jpg", b"\xff\xd8x", "image/jpeg")}
    )

    assert response.status_code == 200
    text = response.text
    assert api.paths() == ["/api/v1/invoices/recognize"]
    for expected in ("12345678", "04595257", "辦公文具", "影印紙", "買受人統編未辨識，請輸入", RID):
        assert expected in text, expected
    assert text.count('name="item_source_line_no"') == 2
    assert "備註" in text and "API 目前沒有備註欄位" in text  # 備註只顯示不送出
    assert (
        "不會存入資料庫" not in text and "不留存" not in text
    )  # 不沿用展示頁「不保存圖檔」的錯誤說明


@pytest.mark.parametrize(
    "files,expected_status",
    [
        (None, 400),
        ({"file": ("", b"", "application/octet-stream")}, 400),
        ({"file": ("a.jpg", b"", "image/jpeg")}, 400),
    ],
)
def test_missing_or_empty_file_never_reaches_api(
    web: TestClient, api: FakeApi, files: Any, expected_status: int
) -> None:
    response = web.post("/intake/recognize", headers=ORIGIN, files=files)

    assert response.status_code == expected_status and api.requests == []


def test_oversized_file_is_rejected_before_api(web: TestClient, api: FakeApi) -> None:
    big = b"x" * (intake_service.MAX_UPLOAD_BYTES + 1)

    response = web.post(
        "/intake/recognize", headers=ORIGIN, files={"file": ("a.jpg", big, "image/jpeg")}
    )

    assert response.status_code == 400 and "4 MB" in response.text and api.requests == []


def test_recognize_api_errors_show_a_message_not_a_form(web: TestClient, api: FakeApi) -> None:
    api.routes["/api/v1/invoices/recognize"] = httpx.Response(
        400,
        json={
            "error": {"code": "INVOICE_PDF_MULTIPAGE", "message": "PDF 只能有一頁", "details": None}
        },
    )

    response = web.post(
        "/intake/recognize", headers=ORIGIN, files={"file": ("a.pdf", b"%PDF", "application/pdf")}
    )

    assert (
        response.status_code == 400
        and "PDF 只能有一頁" in response.text
        and "核對發票資料" not in response.text
    )


def test_recognize_server_error_uses_generic_text(web: TestClient, api: FakeApi) -> None:
    api.routes["/api/v1/invoices/recognize"] = httpx.Response(
        503,
        json={"error": {"code": "X", "message": "內部細節 db connection pool", "details": None}},
    )

    response = web.post(
        "/intake/recognize", headers=ORIGIN, files={"file": ("a.jpg", b"x", "image/jpeg")}
    )

    assert (
        response.status_code == 502
        and "db connection" not in response.text
        and "稍後再試" in response.text
    )


# ---- 核對、檢核 ----


def test_check_calls_the_three_api_checks_and_shows_results(web: TestClient, api: FakeApi) -> None:
    api.routes["/api/v1/invoices/check-required"] = httpx.Response(
        200,
        json={
            "required_complete": False,
            "missing": [
                {
                    "field": "invoice_date",
                    "code": "required_missing",
                    "message": "請輸入發票日期",
                    "line_no": None,
                },
                {
                    "field": "item_name",
                    "code": "required_missing",
                    "message": "請輸入品名",
                    "line_no": 2,
                },
            ],
        },
    )
    api.routes["/api/v1/invoices/check-warnings"] = httpx.Response(
        200,
        json={
            "warnings": [
                {
                    "field": "seller_tax_id",
                    "code": "tax_id_checksum",
                    "message": "銷貨人統編檢查碼不符，請確認",
                }
            ],
            "checked_on": "2026-11-02",
        },
    )
    api.routes["/api/v1/invoices/check-duplicate"] = httpx.Response(
        200, json={"is_duplicate": True}
    )

    response = web.post("/intake/review", headers=ORIGIN, data=review_data("check"))

    assert response.status_code == 200
    assert api.paths() == [
        "/api/v1/invoices/check-required",
        "/api/v1/invoices/check-warnings",
        "/api/v1/invoices/check-duplicate",
    ]
    for expected in (
        "請輸入發票日期",
        "請輸入品名",
        "銷貨人統編檢查碼不符，請確認",
        "系統內已有相同字軌",
    ):
        assert expected in response.text, expected
    assert "recognition_id" not in api.last_json("/api/v1/invoices/check-required")


def test_check_failure_shows_banner_and_keeps_user_input(web: TestClient, api: FakeApi) -> None:
    api.routes["/api/v1/invoices/check-warnings"] = httpx.Response(
        500, json={"error": {"code": "INTERNAL_ERROR", "message": "boom", "details": None}}
    )

    response = web.post(
        "/intake/review", headers=ORIGIN, data=review_data("check", buyer_name="使用者輸入")
    )

    assert (
        response.status_code == 502
        and "使用者輸入" in response.text
        and "boom" not in response.text
    )


def test_add_and_remove_item_do_not_call_api(web: TestClient, api: FakeApi) -> None:
    added = web.post("/intake/review", headers=ORIGIN, data=review_data("add_item"))
    removed = web.post("/intake/review", headers=ORIGIN, data=review_data("remove_item:0"))

    assert added.text.count('name="item_item_name"') == 3 and "新增" in added.text
    assert (
        removed.text.count('name="item_item_name"') == 1
        and "辦公文具" not in removed.text.split("品名明細")[1]
    )
    assert api.requests == []


def test_unknown_action_is_400(web: TestClient) -> None:
    assert (
        web.post("/intake/review", headers=ORIGIN, data=review_data("approve")).status_code == 400
    )


def test_user_input_is_html_escaped(web: TestClient) -> None:
    response = web.post(
        "/intake/review",
        headers=ORIGIN,
        data=review_data("add_item", seller_name='"><script>alert(1)</script>'),
    )

    assert "<script>alert(1)</script>" not in response.text and "&lt;script&gt;" in response.text


# ---- 提交 ----


def test_submit_success_sends_a_clean_body_and_shows_the_case(
    web: TestClient, api: FakeApi
) -> None:
    data = review_data(
        "submit",
        user_id="evil",
        department_id="evil",
        submitted_by="evil",
        invoice_number="１２３４５６７８",
        sales_amount="3,000",
    )

    response = web.post("/intake/review", headers=ORIGIN, data=data)

    assert (
        response.status_code == 200 and str(INV) in response.text and "待會計覆核" in response.text
    )
    body = api.last_json("/api/v1/invoices")
    assert body["recognition_id"] == RID and body["changes"] == []
    assert body["invoice_number"] == "12345678" and body["sales_amount"] == "3000"
    assert body["buyer_tax_id"] is None
    assert body["items"][0] == {
        "source_line_no": 1,
        "item_name": "辦公文具",
        "quantity": "2",
        "unit_price": "1000",
        "line_amount": "2000",
    }
    assert not {"user_id", "department_id", "submitted_by"} & set(
        body
    )  # 身分只取自已驗證登入，不採用表單
    assert api.paths() == ["/api/v1/invoices"]  # 提交本身不自動連打檢核


def test_submit_422_marks_the_failing_fields(web: TestClient, api: FakeApi) -> None:
    api.routes["/api/v1/invoices"] = httpx.Response(
        422,
        json={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "請求參數格式錯誤",
                "details": [
                    {
                        "loc": ["body", "seller_tax_id"],
                        "type": "string_pattern_mismatch",
                        "msg": "欄位格式錯誤",
                    },
                    {
                        "loc": ["body", "items", 1, "line_amount"],
                        "type": "decimal_parsing",
                        "msg": "欄位格式錯誤",
                    },
                ],
            }
        },
    )

    response = web.post(
        "/intake/review", headers=ORIGIN, data=review_data("submit", seller_tax_id="bad")
    )

    assert response.status_code == 422 and response.text.count("格式或內容不正確，請確認") == 2
    assert 'value="bad"' in response.text  # 使用者輸入保留


def test_submit_duplicate_invoice_is_a_clear_error_not_success(
    web: TestClient, api: FakeApi
) -> None:
    api.routes["/api/v1/invoices"] = httpx.Response(
        409,
        json={
            "error": {
                "code": "INVOICE_DUPLICATED",
                "message": "此發票已存在於系統中，禁止重複上傳",
                "details": None,
            }
        },
    )

    response = web.post("/intake/review", headers=ORIGIN, data=review_data("submit"))

    assert (
        response.status_code == 400
        and "禁止重複上傳" in response.text
        and "發票已提交" not in response.text
    )


def test_submit_timeout_confirms_via_recognition_lookup(
    web: TestClient, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.routes["/api/v1/invoices"] = httpx.ReadTimeout("slow")
    seen: list[tuple[str, UUID]] = []

    def found(recognition_id: str, user_id: UUID, **kwargs: Any) -> UUID:
        seen.append((recognition_id, user_id))
        return INV

    monkeypatch.setattr(intake_service, "confirm_submitted", found)

    response = web.post("/intake/review", headers=ORIGIN, data=review_data("submit"))

    assert (
        response.status_code == 200
        and str(INV) in response.text
        and "沒有重複建立" in response.text
    )
    assert seen == [(RID, USER)]  # 只查本人（已驗證身分）的辨識


def test_submit_timeout_with_no_match_says_not_confirmed(
    web: TestClient, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.routes["/api/v1/invoices"] = httpx.ReadTimeout("slow")
    monkeypatch.setattr(intake_service, "confirm_submitted", lambda *a, **k: None)

    response = web.post("/intake/review", headers=ORIGIN, data=review_data("submit"))

    assert (
        response.status_code == 502
        and "尚未確認是否已送出" in response.text
        and "發票已提交" not in response.text
    )


def test_connect_failure_is_a_plain_error_without_lookup(
    web: TestClient, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.routes["/api/v1/invoices"] = httpx.ConnectError("down")
    monkeypatch.setattr(
        intake_service, "confirm_submitted", lambda *a, **k: pytest.fail("不該查證")
    )

    response = web.post("/intake/review", headers=ORIGIN, data=review_data("submit"))

    assert response.status_code == 502 and "發票已提交" not in response.text


def test_already_submitted_recognition_recovers_the_existing_case(
    web: TestClient, api: FakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.routes["/api/v1/invoices"] = httpx.Response(
        409,
        json={
            "error": {
                "code": "RECOGNITION_ALREADY_SUBMITTED",
                "message": "這張發票已送出",
                "details": None,
            }
        },
    )
    monkeypatch.setattr(intake_service, "confirm_submitted", lambda *a, **k: INV)

    response = web.post("/intake/review", headers=ORIGIN, data=review_data("submit"))

    assert (
        response.status_code == 200
        and str(INV) in response.text
        and "沒有重複建立" in response.text
    )


def test_api_authorization_failure_uses_fixed_text(web: TestClient, api: FakeApi) -> None:
    api.routes["/api/v1/invoices"] = httpx.Response(
        401,
        json={"error": {"code": "UNAUTHORIZED", "message": "token digest abc", "details": None}},
    )

    response = web.post("/intake/review", headers=ORIGIN, data=review_data("submit"))

    assert "授權失敗" in response.text and "token digest" not in response.text


def test_credential_value_never_reaches_the_page(web: TestClient, api: FakeApi) -> None:
    response = web.post(
        "/intake/recognize", headers=ORIGIN, files={"file": ("a.jpg", b"x", "image/jpeg")}
    )

    assert "synthetic-test-credential" not in response.text
    assert "synthetic-test-credential" not in "".join(str(v) for v in response.headers.values())
    assert SESSION_COOKIE_NAME not in response.text


# ---- 原圖對照與警示 DOM 接點（A 組） ----


def test_recognize_embeds_the_original_image_only_for_jpg_png(web: TestClient) -> None:
    image = web.post(
        "/intake/recognize", headers=ORIGIN, files={"file": ("a.jpg", b"\xff\xd8x", "image/jpeg")}
    )
    pdf = web.post(
        "/intake/recognize", headers=ORIGIN, files={"file": ("a.pdf", b"%PDF-1", "application/pdf")}
    )

    assert 'src="data:image/jpeg;base64,' in image.text
    assert '<script src="/static/js/intake-original.js' in image.text  # 核對頁載入原圖還原腳本
    assert 'id="intake-original-img"' in image.text and 'id="intake-original-note"' in image.text
    assert 'id="intake-original"' in image.text and f'data-recognition-id="{RID}"' in image.text
    assert 'src="data:' not in pdf.text and "PDF 檔案無法在此頁內嵌顯示" in pdf.text


def test_check_marks_blocking_and_warning_with_stable_hooks(web: TestClient, api: FakeApi) -> None:
    api.routes["/api/v1/invoices/check-required"] = httpx.Response(
        200,
        json={
            "required_complete": False,
            "missing": [
                {"field": "invoice_date", "code": "required_missing", "message": "請輸入發票日期"}
            ],
        },
    )
    api.routes["/api/v1/invoices/check-warnings"] = httpx.Response(
        200,
        json={
            "warnings": [
                {"field": "seller_tax_id", "code": "tax_id_checksum", "message": "統編檢查碼不符"}
            ],
            "checked_on": "2026-11-02",
        },
    )

    text = web.post("/intake/review", headers=ORIGIN, data=review_data("check")).text

    assert 'id="intake-check-results"' in text and 'data-state="checked"' in text
    assert 'data-required-count="1"' in text and 'data-warning-count="1"' in text
    assert 'data-check-field="invoice_date"' in text
    assert 'data-check-kind="required" data-severity="blocking"' in text
    assert 'data-check-kind="warning" data-severity="warning"' in text
    assert 'data-duplicate="no"' in text


def test_unchecked_page_and_unknown_duplicate_are_not_shown_as_passed(
    web: TestClient, api: FakeApi
) -> None:
    unchecked = web.post("/intake/review", headers=ORIGIN, data=review_data("add_item")).text
    assert 'data-state="unchecked"' in unchecked and "檢核完成" not in unchecked

    text = web.post(
        "/intake/review", headers=ORIGIN, data=review_data("check", invoice_number="")
    ).text
    assert 'data-duplicate="unknown"' in text and "未執行查重" in text
    assert "沒有發現相同發票" not in text
