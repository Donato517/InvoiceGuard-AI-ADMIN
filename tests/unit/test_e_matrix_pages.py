"""單一部門權限內容頁驗收；合成矩陣及 Session，禁止真實 DB／網路。"""

from collections.abc import Iterator
from dataclasses import replace
from html.parser import HTMLParser
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.repositories import e_function_settings_repository as repository
from app.main import app
from app.routers.e_admin import require_configuration_access
from app.schemas.e_admin import Action, ConflictError, EError, MissingError
from app.schemas.e_function_settings import (
    DepartmentFunctionSettings,
    FunctionPermission,
    FunctionSetting,
)
from app.services import department_service
from app.services import e_function_settings_service as service

DEPT = UUID("00000000-0000-4000-8000-000000000001")
ONE = UUID("00000000-0000-4000-8000-000000000002")
TWO = UUID("00000000-0000-4000-8000-000000000003")
BASE = "http://invoiceguard-ai-admin.localhost:8080"
ORIGIN = {"Origin": BASE}
ROWS = (
    FunctionPermission(
        FunctionSetting(ONE, "sys.features", "功能管理", "admin", True), frozenset({Action.VIEW})
    ),
    FunctionPermission(
        FunctionSetting(TWO, "sys.departments", "部門管理", "both", True),
        frozenset({Action.VIEW, Action.EDIT}),
    ),
)
MATRIX = DepartmentFunctionSettings(DEPT, "SYS", "系統管理", True, ROWS)


class Forms(HTMLParser):
    """從實際 HTML 取欄位，避免把模板空白或手寫提交當成流程驗證。"""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.forms: dict[str, dict[str, list[str]]] = {}
        self.checks: dict[str, dict[str, str | None]] = {}
        self.current: dict[str, list[str]] | None = None
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "form":
            self.current = self.forms.setdefault(data.get("action", ""), {})
        if tag != "input" or self.current is None or not data.get("name"):
            return
        if data.get("type") == "checkbox":
            self.checks[data.get("value", "")] = data
            if "checked" not in data or "disabled" in data:
                return
        self.current.setdefault(data["name"], []).append(data.get("value", ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.current = None


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """交易與 Repository 邊界保留，連線改成可觀察替身。"""
    db = MagicMock()
    monkeypatch.setattr(repository, "open_session", lambda: db)
    monkeypatch.setattr(repository, "prepare_write", MagicMock())
    monkeypatch.setattr(repository, "read_matrix", MagicMock(return_value=MATRIX))
    monkeypatch.setattr(repository, "write_permission", MagicMock())
    monkeypatch.setattr(department_service, "search", lambda: [])
    return db


@pytest.fixture
def browser(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """以合成配置授權測畫面，不覆寫 Host 或同源檢查。"""
    # 僅隔離業務 UI；正式驗權與未接線拒絕行為由 test_e_login_access 驗證。
    monkeypatch.setitem(app.dependency_overrides, require_configuration_access, lambda: None)
    with TestClient(
        app, base_url=BASE, client=("127.0.0.1", 50000), follow_redirects=False
    ) as client:
        yield client


def payload(*, confirmed: bool = False) -> dict[str, str | list[str]]:
    """只提交第一列，測試增添修改與撤銷查看並保留另一列。"""
    values = {
        "function_id": [str(ONE)],
        "grant": [f"{ONE}:edit"],
        "expected_revision": service.content_revision(MATRIX),
        "return_q": "功能",
        "return_platform": "admin",
        "return_status": "active",
        "return_page": "1",
    }
    if confirmed:
        values["confirmed"] = "true"
    return values


def test_content_filters_show_only_matches_without_writing(
    browser: TestClient, db: MagicMock
) -> None:
    page = browser.get(
        "/department-functions",
        params={"department_id": str(DEPT), "q": "功能", "platform": "admin", "status": "active"},
    )
    assert page.status_code == 200
    forms = Forms(page.text)
    assert forms.forms[f"/department-functions/{DEPT}/preview"]["function_id"] == [str(ONE)]
    assert "資料範圍待接線" in page.text and "功能未提供此操作" in page.text
    assert "data-permission-editor" in page.text and "e-department-permissions.js" in page.text
    assert page.headers["cache-control"] == "no-store" and "set-cookie" not in page.headers
    db.commit.assert_not_called()


def test_preview_summarizes_add_and_revoke_but_never_writes(
    browser: TestClient, db: MagicMock
) -> None:
    page = browser.post(f"/department-functions/{DEPT}/preview", data=payload(), headers=ORIGIN)
    assert page.status_code == 200
    assert "新增操作" in page.text and "撤銷操作" in page.text and "確認儲存" in page.text
    forms = Forms(page.text).forms
    assert forms[f"/department-functions/{DEPT}"]["confirmed"] == ["true"]
    assert forms[f"/department-functions/{DEPT}"]["grant"] == [f"{ONE}:edit"]
    assert "confirmed" not in forms[f"/department-functions/{DEPT}/draft"]
    repository.write_permission.assert_not_called()
    db.commit.assert_not_called()


def test_actual_html_round_trip_preserves_draft_and_only_saves_submitted_rows(
    browser: TestClient, db: MagicMock
) -> None:
    editor = browser.get(f"/department-functions?department_id={DEPT}&q=功能")
    values = Forms(editor.text).forms[f"/department-functions/{DEPT}/preview"]
    values["grant"] = [f"{ONE}:edit"]
    preview = browser.post(f"/department-functions/{DEPT}/preview", headers=ORIGIN, data=values)
    assert preview.status_code == 200
    forms = Forms(preview.text).forms
    draft = browser.post(
        f"/department-functions/{DEPT}/draft",
        headers=ORIGIN,
        data=forms[f"/department-functions/{DEPT}/draft"],
    )
    assert draft.status_code == 200
    returned = Forms(draft.text)
    assert "checked" in returned.checks[f"{ONE}:edit"]
    assert returned.checks[f"{ONE}:edit"]["data-saved"] == "false"
    assert "checked" not in returned.checks[f"{ONE}:view"]
    assert "有尚未儲存的變更" in draft.text
    db.commit.assert_not_called()
    saved = browser.post(
        f"/department-functions/{DEPT}", headers=ORIGIN, data=forms[f"/department-functions/{DEPT}"]
    )
    assert saved.status_code == 303
    repository.write_permission.assert_called_once_with(db, DEPT, ONE, frozenset({Action.EDIT}))
    location = urlsplit(saved.headers["location"])
    assert location.path == "/department-functions" and not location.netloc
    assert parse_qs(location.query)["q"] == ["功能"]
    assert parse_qs(location.query)["department_id"] == [str(DEPT)]
    db.commit.assert_called_once()


def test_all_unchecked_is_an_explicit_revoke_not_a_delete(
    browser: TestClient, db: MagicMock
) -> None:
    data = payload(confirmed=True)
    data.pop("grant")
    assert (
        browser.post(f"/department-functions/{DEPT}", headers=ORIGIN, data=data).status_code == 303
    )
    repository.write_permission.assert_called_once_with(db, DEPT, ONE, frozenset())


def test_unchanged_preview_has_no_save_button(browser: TestClient, db: MagicMock) -> None:
    data = payload()
    data["grant"] = [f"{ONE}:view"]
    page = browser.post(f"/department-functions/{DEPT}/preview", headers=ORIGIN, data=data)
    assert page.status_code == 200 and "沒有權限變更" in page.text
    assert f"/department-functions/{DEPT}" not in Forms(page.text).forms
    db.commit.assert_not_called()


@pytest.mark.parametrize("stage", ["preview", "draft", "save"])
def test_stale_content_blocks_every_stage_and_keeps_original_revision(
    browser: TestClient, db: MagicMock, stage: str
) -> None:
    data = payload(confirmed=stage == "save")
    data["expected_revision"] = "0" * 64
    path = f"/department-functions/{DEPT}" + ("/" + stage if stage != "save" else "")
    page = browser.post(path, headers=ORIGIN, data=data)
    assert page.status_code == 409 and "重新載入目前設定" in page.text
    editor = Forms(page.text)
    assert editor.forms[f"/department-functions/{DEPT}/preview"]["expected_revision"] == ["0" * 64]
    assert "disabled" in editor.checks[f"{ONE}:edit"]
    repository.write_permission.assert_not_called()
    db.commit.assert_not_called()


def test_concurrent_revoke_after_preview_is_not_overwritten(
    browser: TestClient, db: MagicMock
) -> None:
    page = browser.post(f"/department-functions/{DEPT}/preview", headers=ORIGIN, data=payload())
    form = Forms(page.text).forms[f"/department-functions/{DEPT}"]
    repository.read_matrix.return_value = replace(
        MATRIX, rows=(replace(ROWS[0], actions=frozenset()), ROWS[1])
    )
    assert (
        browser.post(f"/department-functions/{DEPT}", headers=ORIGIN, data=form).status_code == 409
    )
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "kind,reason",
    [("inactive", "功能已停用"), ("frontend", "不適用管理站"), ("unknown", "程式能力尚未登錄")],
)
def test_blocked_features_explain_reason_allow_only_existing_revoke(
    browser: TestClient, db: MagicMock, kind: str, reason: str
) -> None:
    function = ROWS[0].function
    if kind == "inactive":
        function = replace(function, is_active=False)
    elif kind == "frontend":
        function = replace(function, app_scope="frontend")
    else:
        function = replace(function, function_code="future.unreviewed")
    matrix = replace(MATRIX, rows=(replace(ROWS[0], function=function), ROWS[1]))
    repository.read_matrix.return_value = matrix
    page = browser.get(f"/department-functions?department_id={DEPT}")
    checks = Forms(page.text).checks
    assert reason in page.text and "原有設定可撤銷" in page.text
    assert "disabled" not in checks[f"{ONE}:view"]
    assert "disabled" in checks[f"{ONE}:edit"]
    with pytest.raises(EError):
        service.review_matrix(
            DEPT, service.content_revision(matrix), {ONE: frozenset({Action.VIEW, Action.EDIT})}
        )
    service.save_matrix(DEPT, service.content_revision(matrix), {ONE: frozenset()})
    repository.write_permission.assert_called_once_with(db, DEPT, ONE, frozenset())


def test_disabled_department_is_read_only(browser: TestClient, db: MagicMock) -> None:
    matrix = replace(MATRIX, is_active=False)
    repository.read_matrix.return_value = matrix
    page = browser.get(f"/department-functions?department_id={DEPT}")
    assert page.status_code == 200 and "僅供查看" in page.text
    assert all("disabled" in value for value in Forms(page.text).checks.values())
    with pytest.raises(ConflictError):
        service.review_matrix(DEPT, service.content_revision(matrix), {ONE: frozenset()})
    db.commit.assert_not_called()


def test_matrix_pagination_keeps_twenty_rows_and_filter_context(browser: TestClient) -> None:
    rows = tuple(
        FunctionPermission(
            replace(
                ROWS[0].function,
                function_id=UUID(int=index + 100),
                function_code=f"future.f{index:02}",
            ),
            frozenset(),
        )
        for index in range(45)
    )
    repository.read_matrix.return_value = replace(MATRIX, rows=rows)
    page = browser.get(
        f"/department-functions?department_id={DEPT}&q=future&platform=admin&status=active&page=2"
    )
    assert page.status_code == 200 and "共 45 筆" in page.text
    form = Forms(page.text).forms[f"/department-functions/{DEPT}/preview"]
    assert len(form["function_id"]) == 20 and form["function_id"][0] == str(
        rows[20].function.function_id
    )
    assert form["return_page"] == ["2"] and form["return_q"] == ["future"]
    last = browser.get(f"/department-functions?department_id={DEPT}&page=99")
    assert "第 3／3 頁" in last.text


@pytest.mark.parametrize("query", ["q=missing", "platform=frontend", "status=inactive"])
def test_empty_filtered_result_does_not_offer_a_save(browser: TestClient, query: str) -> None:
    page = browser.get(f"/department-functions?department_id={DEPT}&{query}")
    assert page.status_code == 200 and "沒有符合條件" in page.text
    assert f"/department-functions/{DEPT}/preview" not in Forms(page.text).forms


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "admin"),
        ("next", "https://example.invalid"),
        ("return_page", "0"),
        ("grant", [f"{TWO}:edit"]),
        ("function_id", [str(ONE), str(ONE)]),
        ("function_id", []),
        ("grant", [f"{ONE}:approve"]),
        ("expected_revision", "bad"),
    ],
)
def test_tampered_payload_cannot_write(
    browser: TestClient, db: MagicMock, field: str, value: object
) -> None:
    data = payload()
    data[field] = value
    assert (
        browser.post(f"/department-functions/{DEPT}/preview", headers=ORIGIN, data=data).status_code
        == 400
    )
    db.commit.assert_not_called()
    repository.write_permission.assert_not_called()


def test_final_save_requires_explicit_confirmation(browser: TestClient, db: MagicMock) -> None:
    assert (
        browser.post(f"/department-functions/{DEPT}", headers=ORIGIN, data=payload()).status_code
        == 400
    )
    db.commit.assert_not_called()


@pytest.mark.parametrize("stage", ["preview", "draft", "save"])
def test_every_post_requires_origin_and_stays_closed_in_cloud(
    browser: TestClient, db: MagicMock, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    path = f"/department-functions/{DEPT}" + ("/" + stage if stage != "save" else "")
    data = payload(confirmed=stage == "save")
    assert browser.post(path, data=data).status_code == 403
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert browser.post(path, headers=ORIGIN, data=data).status_code == 404
    db.commit.assert_not_called()


def test_missing_department_is_404_and_read_failure_is_not_empty(
    browser: TestClient, db: MagicMock
) -> None:
    repository.read_matrix.side_effect = MissingError()
    assert browser.get(f"/department-functions?department_id={DEPT}").status_code == 404
    repository.read_matrix.side_effect = RuntimeError("synthetic-private-db")
    page = browser.get(f"/department-functions?department_id={DEPT}")
    assert page.status_code == 503 and "重新載入目前設定" in page.text
    assert "synthetic-private" not in page.text and "目前沒有功能" not in page.text
    db.commit.assert_not_called()


def test_failed_commit_shows_no_success_and_does_not_rebase_revision(
    browser: TestClient, db: MagicMock
) -> None:
    db.commit.side_effect = RuntimeError("synthetic-private-write")
    page = browser.post(
        f"/department-functions/{DEPT}", headers=ORIGIN, data=payload(confirmed=True)
    )
    assert page.status_code == 503 and "未確認成功" in page.text
    assert "部門功能設定已儲存" not in page.text and "synthetic-private" not in page.text
    assert Forms(page.text).forms[f"/department-functions/{DEPT}/preview"]["expected_revision"] == [
        service.content_revision(MATRIX)
    ]
    db.rollback.assert_called()


def test_preview_escapes_department_and_function_names(browser: TestClient) -> None:
    matrix = replace(
        MATRIX,
        department_name="<script>synthetic()</script>",
        rows=(replace(ROWS[0], function=replace(ROWS[0].function, function_name="<img src=x>")),),
    )
    repository.read_matrix.return_value = matrix
    data = payload()
    data["expected_revision"] = service.content_revision(matrix)
    page = browser.post(f"/department-functions/{DEPT}/preview", headers=ORIGIN, data=data)
    assert page.status_code == 200 and "&lt;script&gt;" in page.text and "&lt;img" in page.text
    assert "<script>synthetic()" not in page.text
