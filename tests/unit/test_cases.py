"""測試目標：ADMIN 真實案件唯讀檢視（/cases）——權限、預設拒絕、錯誤對應、原圖標頭、只讀；合成資料，不連 DB／Storage。

注意事項：Service 以替身 Session／Storage 注入；SQL 本身由 tests/integration/test_cases_isolated_db.py
          在隔離 PostgreSQL 驗證。
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.constants import SESSION_COOKIE_NAME
from app.routers import cases as cases_router
from app.schemas.case import (
    CaseDataInvalidError,
    CaseDetail,
    CaseFileInfo,
    CaseFileMissingError,
    CaseHeader,
    CaseItem,
    CaseNoImageError,
    CaseNotFoundError,
    CasePage,
    CaseScope,
    CaseStorageError,
    CaseStorageUnavailableError,
    CaseSummary,
    CaseUnavailableError,
    CaseWarning,
)
from app.schemas.e_access import EffectiveDepartment
from app.schemas.e_admin import Action, DeniedError, UnauthenticatedError
from app.services import case_service
from app.services.e_admin_service import INVOICES, get_e_module

USER = UUID("00000000-0000-4000-8000-000000000001")
DEPT = UUID("00000000-0000-4000-8000-0000000000d1")
OTHER_DEPT = UUID("00000000-0000-4000-8000-0000000000d2")
CASE = UUID("00000000-0000-4000-8000-0000000000c1")
NOW = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)


class FakeModule:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.checks: list[tuple[Any, ...]] = []

    def identity(self, token: Any) -> SimpleNamespace:
        if token is None:
            raise UnauthenticatedError
        return SimpleNamespace(user_id=USER, selected_department_id=DEPT)

    def check(self, identity: Any, code: str, action: Action, target: Any) -> None:
        self.checks.append((code, action, target.department_id))
        if not self.allowed:
            raise DeniedError

    def departments(self, identity: Any) -> tuple[EffectiveDepartment, ...]:
        return (EffectiveDepartment(DEPT, "procurement_d", "採購部"),)

    def sidebar_menu(self, identity: Any) -> tuple[()]:
        return ()


class AllowDepartment:
    """測試用政策：只能讀自己目前部門送出的案件（候選規則，未核定，正式程式不使用）。"""

    def scope(self, identity: Any, departments: Any) -> CaseScope:
        return CaseScope("department", identity.selected_department_id)


def _header(**overrides: Any) -> CaseHeader:
    base: dict[str, Any] = {
        "invoice_id": CASE,
        "invoice_track": "AB",
        "invoice_number": "12345678",
        "invoice_date": None,
        "seller_name": "ABC股份有限公司",
        "total_amount": Decimal("3150.00"),
        "status": "pending_department_approval",
        "submission_round": 1,
        "submitted_by": USER,
        "submitter_name": "buyer1",
        "department_id": DEPT,
        "department_name": "採購部",
        "submitted_at": NOW,
        "tax_type": "taxable",
        "validation_results": {"warnings": []},
    }
    return CaseHeader(**{**base, **overrides})


def _detail(*, with_file: bool = True) -> CaseDetail:
    return CaseDetail(
        header=_header(),
        items=(CaseItem(CASE, 1, "辦公文具", Decimal("2"), Decimal("1000"), Decimal("2000")),),
        file=CaseFileInfo(CASE, "inv.jpg", "image/jpeg", 12, 1, NOW) if with_file else None,
        warnings=(CaseWarning("total_amount", "total_mismatch", "總計不一致"),),
    )


@pytest.fixture
def module() -> FakeModule:
    return FakeModule()


@pytest.fixture
def web(module: FakeModule) -> Iterator[TestClient]:
    from app.main import app

    app.dependency_overrides[get_e_module] = lambda: module
    app.dependency_overrides[case_service.get_case_policy] = AllowDepartment
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(SESSION_COOKIE_NAME, "synthetic-session")
    yield client
    app.dependency_overrides.clear()


# ---- 範圍與預設拒絕 ----


def test_scope_rules() -> None:
    assert not CaseScope().allows(DEPT)
    assert CaseScope("all").allows(None)
    assert CaseScope("department", DEPT).allows(DEPT)
    assert not CaseScope("department", DEPT).allows(OTHER_DEPT)
    assert not CaseScope("department", DEPT).allows(None)
    assert not CaseScope("department", None).allows(DEPT)


def test_default_policy_denies_everyone() -> None:
    policy = case_service.get_case_policy()

    assert isinstance(policy, case_service.DenyAllCasePolicy)
    assert policy.scope(SimpleNamespace(), ()) == CaseScope()


def test_services_deny_before_touching_db_or_storage() -> None:
    def boom() -> Any:
        raise AssertionError("不應開啟資料庫")

    scope = CaseScope()
    with pytest.raises(DeniedError):
        case_service.list_cases(scope, page=1, page_size=20, status=None, session_factory=boom)
    with pytest.raises(DeniedError):
        case_service.get_case_detail(scope, CASE, session_factory=boom)
    with pytest.raises(DeniedError):
        case_service.get_case_image(scope, CASE, session_factory=boom)


def test_list_rejects_unknown_status_and_draft() -> None:
    for status in ("bogus", "draft"):
        with pytest.raises(CaseNotFoundError):
            case_service.list_cases(CaseScope("all"), page=1, page_size=20, status=status)


def test_validation_results_parsing() -> None:
    assert case_service.parse_warnings(None) == ()
    assert case_service.parse_warnings({}) == ()
    assert case_service.parse_warnings(
        {"warnings": [{"field": "a", "code": "b", "message": "c"}]}
    ) == (CaseWarning("a", "b", "c"),)
    for bad in ([], {"warnings": "x"}, {"warnings": [1]}, {"warnings": [{"field": "a"}]}):
        with pytest.raises(CaseDataInvalidError):
            case_service.parse_warnings(bad)


# ---- 路由：驗證與權限 ----


def test_unauthenticated_cannot_read(module: FakeModule) -> None:
    from app.main import app

    app.dependency_overrides[get_e_module] = lambda: module
    try:
        client = TestClient(app, follow_redirects=False)  # 沒有 Cookie
        for path in ("/cases", f"/cases/{CASE}", f"/cases/{CASE}/image"):
            assert client.get(path).status_code in (401, 303), path
    finally:
        app.dependency_overrides.clear()


def test_function_permission_is_required(web: TestClient, module: FakeModule) -> None:
    module.allowed = False

    for path in ("/cases", f"/cases/{CASE}", f"/cases/{CASE}/image"):
        assert web.get(path).status_code == 403, path
    assert all(check[:2] == (INVOICES, Action.VIEW) for check in module.checks)


def test_default_policy_gives_403_even_with_function_permission(module: FakeModule) -> None:
    from app.main import app

    app.dependency_overrides[get_e_module] = lambda: module  # 不覆寫政策＝正式預設
    try:
        client = TestClient(app, follow_redirects=False)
        client.cookies.set(SESSION_COOKIE_NAME, "synthetic-session")
        for path in ("/cases", f"/cases/{CASE}", f"/cases/{CASE}/image"):
            assert client.get(path).status_code == 403, path
    finally:
        app.dependency_overrides.clear()


def test_no_write_routes_exist(web: TestClient) -> None:
    for method in ("post", "put", "patch", "delete"):
        for path in ("/cases", f"/cases/{CASE}", "/cases/new", f"/cases/{CASE}/image"):
            assert getattr(web, method)(path).status_code in (403, 404, 405), (method, path)
    cases_methods = {
        method
        for route in cases_router.router.routes
        for method in getattr(route, "methods", set())
    }
    assert cases_methods == {"GET"}


def test_invalid_uuid_is_404_without_service_call(
    web: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(case_service, "get_case_detail", lambda *a, **k: pytest.fail("called"))

    assert web.get("/cases/not-a-uuid").status_code == 404
    assert web.get("/cases/not-a-uuid/image").status_code == 404


# ---- 路由：資料與錯誤 ----


def test_list_page_renders_real_fields(web: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = CaseSummary(
        **{k: v for k, v in _header().__dict__.items() if k in CaseSummary.__dataclass_fields__}
    )
    seen: dict[str, Any] = {}

    def fake_list(scope: CaseScope, **kwargs: Any) -> CasePage:
        seen.update(scope=scope, **kwargs)
        return CasePage((summary,), 1, 1, 20)

    monkeypatch.setattr(case_service, "list_cases", fake_list)

    response = web.get("/cases?status=approved")

    assert response.status_code == 200
    assert (
        str(CASE) in response.text
        and "AB12345678" in response.text
        and "待單位核准" in response.text
    )
    assert seen["scope"] == CaseScope("department", DEPT)
    assert seen["status"] == "approved"


def test_detail_page_labels_saved_validation_and_has_no_actions(
    web: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(case_service, "get_case_detail", lambda scope, invoice_id: _detail())

    response = web.get(f"/cases/{CASE}")

    assert response.status_code == 200
    assert f"/cases/{CASE}/image" in response.text
    assert "送出當下保存" in response.text and "總計不一致" in response.text
    # 版面共用的登出表單不算；案件頁本身不得有任何指向 /cases 或審核動作的表單與按鈕
    assert 'action="/cases' not in response.text
    assert "<button" not in response.text.split("<h2>案件")[1].split("</main>")[0]


def test_detail_without_file_shows_warning_not_broken_image(
    web: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        case_service, "get_case_detail", lambda scope, invoice_id: _detail(with_file=False)
    )

    response = web.get(f"/cases/{CASE}")

    assert "沒有目前有效的影像紀錄" in response.text
    assert "<img" not in response.text.split("原始影像")[1].split("</section>")[0]


@pytest.mark.parametrize(
    "error,status",
    [
        (CaseNotFoundError(), 404),
        (CaseNoImageError(), 404),
        (CaseFileMissingError(), 404),
        (CaseStorageError(), 502),
        (CaseStorageUnavailableError(), 503),
        (CaseDataInvalidError(), 500),
    ],
)
def test_service_errors_are_not_fake_success(
    web: TestClient, monkeypatch: pytest.MonkeyPatch, error: Exception, status: int
) -> None:
    def raises(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(case_service, "get_case_detail", raises)
    monkeypatch.setattr(case_service, "get_case_image", raises)

    assert web.get(f"/cases/{CASE}").status_code == status
    assert web.get(f"/cases/{CASE}/image").status_code == status


def test_image_response_is_private_and_typed(
    web: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        case_service, "get_case_image", lambda scope, invoice_id: (b"\xff\xd8\xff", "image/jpeg")
    )

    response = web.get(f"/cases/{CASE}/image")

    assert response.status_code == 200 and response.content == b"\xff\xd8\xff"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "location" not in response.headers


def test_image_route_takes_no_path_parameter() -> None:
    image_route = next(r for r in cases_router.router.routes if str(r.path).endswith("/image"))

    assert {p.name for p in image_route.dependant.path_params} == {"invoice_id"}  # type: ignore[attr-defined]
    assert not image_route.dependant.query_params  # type: ignore[attr-defined]


# ---- 任務一：PDF 不用 iframe；任務二：原圖失敗的三類提示 ----


def _detail_with_mime(mime: str) -> CaseDetail:
    detail = _detail()
    return CaseDetail(
        header=detail.header,
        items=detail.items,
        file=CaseFileInfo(CASE, "inv", mime, 12, 1, NOW),
    )


def test_pdf_opens_in_new_tab_without_iframe(
    web: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        case_service,
        "get_case_detail",
        lambda scope, invoice_id: _detail_with_mime("application/pdf"),
    )

    text = web.get(f"/cases/{CASE}").text

    assert "<iframe" not in text and "<object" not in text and "<embed" not in text
    link = f'href="/cases/{CASE}/image" target="_blank" rel="noopener"'
    assert link in text  # 仍經 /image 驗權；沒有 Storage 路徑
    assert "<img" not in text.split("原始影像")[1].split("</section>")[0]


@pytest.mark.parametrize("mime", ["image/jpeg", "image/png"])
def test_images_still_render_inline_with_error_hook(
    web: TestClient, monkeypatch: pytest.MonkeyPatch, mime: str
) -> None:
    monkeypatch.setattr(
        case_service, "get_case_detail", lambda scope, invoice_id: _detail_with_mime(mime)
    )

    text = web.get(f"/cases/{CASE}").text

    assert f'<img src="/cases/{CASE}/image"' in text and "data-case-image" in text
    assert "data-case-image-error" in text and "js/case-image.js" in text
    assert "target=" not in text.split("原始影像")[1].split("</section>")[0]


def test_global_frame_header_is_unchanged() -> None:
    config = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    headers = {h["key"]: h["value"] for rule in config["headers"] for h in rule["headers"]}

    assert headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize(
    "error,code,status",
    [
        (CaseNoImageError(), "no_image", 404),
        (CaseFileMissingError(), "image_unreadable", 404),
        (CaseStorageError(), "image_unreadable", 502),
        (CaseStorageUnavailableError(), "temporary", 503),
        (CaseUnavailableError(), "temporary", 503),
    ],
)
def test_image_failure_json_has_only_fixed_code_and_message(
    web: TestClient, monkeypatch: pytest.MonkeyPatch, error: Exception, code: str, status: int
) -> None:
    def raises(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(case_service, "get_case_image", raises)

    response = web.get(f"/cases/{CASE}/image", headers={"Accept": "application/json"})

    assert response.status_code == status and response.json()["code"] == code
    assert set(response.json()) == {"code", "message"}
    assert response.headers["cache-control"] == "private, no-store"
    # 沒有要求 JSON（例如新分頁開 PDF）時沿用既有錯誤頁
    plain = web.get(f"/cases/{CASE}/image", headers={"Accept": "text/html"})
    assert plain.status_code == status and "text/html" in plain.headers["content-type"]


def test_three_user_facing_messages_are_distinct_and_free_of_internals() -> None:
    messages = {
        CaseNoImageError.message,
        CaseFileMissingError.message,
        CaseStorageError.message,
        CaseStorageUnavailableError.message,
    }

    assert len(messages) == 4
    for message in messages:
        assert not any(
            word in message.lower() for word in ("sql", "bucket", "s3", "invoices/", "key")
        )
