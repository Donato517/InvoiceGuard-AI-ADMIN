"""E 本機功能頁驗收；只有合成資料／Session 替身，不覆寫本機或同源相依。"""

import re
from collections.abc import Iterator
from dataclasses import replace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import settings
from app.db.repositories import e_function_settings_repository as repository
from app.main import app
from app.routers.e_admin import require_configuration_access
from app.schemas.department import Department
from app.schemas.e_admin import (
    Action,
    ConflictError,
    EError,
    FeatureInput,
    MissingError,
    UnavailableError,
)
from app.schemas.e_function_settings import (
    DepartmentFunctionSettings,
    FunctionListQuery,
    FunctionPermission,
    FunctionSetting,
)
from app.services import department_service
from app.services import e_function_settings_service as service

DEPT = UUID("00000000-0000-4000-8000-000000000001")
FIRST = UUID("00000000-0000-4000-8000-000000000002")
SECOND = UUID("00000000-0000-4000-8000-000000000003")
BASE = "http://invoiceguard-ai-admin.localhost:8080"
ORIGIN = {"Origin": BASE}
FUNCTIONS = (
    FunctionSetting(FIRST, "sys.departments", "部門管理", "admin", True),
    FunctionSetting(SECOND, "sys.features", "功能目錄", "admin", True),
)
MATRIX = DepartmentFunctionSettings(
    DEPT,
    "SYS",
    "系統管理",
    True,
    (
        FunctionPermission(FUNCTIONS[0], frozenset({Action.VIEW})),
        FunctionPermission(FUNCTIONS[1], frozenset()),
    ),
)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """保留 service 交易流程，不建立實際資料。"""
    db = MagicMock()
    monkeypatch.setattr(repository, "open_session", lambda: db)
    monkeypatch.setattr(repository, "prepare_write", MagicMock())
    monkeypatch.setattr(repository, "read_matrix", lambda *args, **kwargs: MATRIX)
    monkeypatch.setattr(repository, "get_function", lambda *args, **kwargs: FUNCTIONS[0])
    monkeypatch.setattr(repository, "list_functions", lambda *args: FUNCTIONS)
    monkeypatch.setattr(repository, "count_functions", MagicMock(return_value=len(FUNCTIONS)))
    monkeypatch.setattr(repository, "page_functions", MagicMock(return_value=FUNCTIONS))
    monkeypatch.setattr(repository, "write_permission", MagicMock())
    monkeypatch.setattr(repository, "insert_function", MagicMock())
    monkeypatch.setattr(repository, "update_function", MagicMock())
    return db


@pytest.fixture
def browser(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """以明確的合成配置授權隔離畫面回歸，不代表匿名可用。"""
    monkeypatch.setattr(
        department_service,
        "search",
        lambda: [
            Department(
                department_id=DEPT,
                department_code="SYS",
                department_name="系統管理",
                is_active=True,
                created_at=None,
                updated_at=None,
            )
        ],
    )
    # 僅隔離業務 UI；正式驗權與未接線拒絕行為由 test_e_login_access 驗證。
    monkeypatch.setitem(app.dependency_overrides, require_configuration_access, lambda: None)
    with TestClient(
        app, base_url=BASE, client=("127.0.0.1", 50000), follow_redirects=False
    ) as browser:
        yield browser


@pytest.mark.parametrize(
    "path", ["/functions", "/department-functions", f"/department-functions?department_id={DEPT}"]
)
def test_authorized_ui_does_not_issue_another_session(browser: TestClient, path: str) -> None:
    response = browser.get(path)
    assert response.status_code == (303 if path.startswith("/department-functions") else 200)
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    # 側欄已核定使用原生 POST 登出；存在表單不等於新建 Session。
    if path == "/functions":
        assert response.text.count('action="/logout"') == 1
        assert 'method="post" action="/logout"' in response.text
    assert "/dev/" not in response.text


@pytest.mark.parametrize("environment", ["preview", "production"])
@pytest.mark.parametrize(
    "path", ["/functions", "/department-functions", f"/department-functions/{DEPT}"]
)
def test_cloud_is_closed_before_service(
    browser: TestClient, db: MagicMock, monkeypatch: pytest.MonkeyPatch, environment: str, path: str
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", environment)
    response = browser.post(path, headers=ORIGIN) if path.endswith(str(DEPT)) else browser.get(path)
    assert response.status_code == 404
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "host,peer", [("evil.invalid", "127.0.0.1"), ("invoiceguard-ai-admin.localhost", "192.0.2.1")]
)
def test_host_peer_and_forged_forwarding_cannot_open_pages(host: str, peer: str) -> None:
    with TestClient(app, base_url=f"http://{host}:8080", client=(peer, 50000)) as browser:
        assert (
            browser.get(
                "/functions",
                headers={
                    "X-Forwarded-For": "127.0.0.1",
                    "X-Forwarded-Host": "invoiceguard-ai-admin.localhost",
                },
            ).status_code
            == 404
        )


@pytest.mark.parametrize(
    "headers", [{}, {"Origin": "http://evil.invalid"}, {"Origin": "null"}, {"Origin": "http://["}]
)
def test_mutations_need_exact_origin(
    browser: TestClient, db: MagicMock, headers: dict[str, str]
) -> None:
    assert (
        browser.post(
            "/functions",
            headers=headers,
            data={"function_code": "sys.features", "function_name": "功能"},
        ).status_code
        == 403
    )
    db.commit.assert_not_called()


def test_create_function_does_not_grant_departments(browser: TestClient, db: MagicMock) -> None:
    response = browser.post(
        "/functions",
        headers=ORIGIN,
        data={"function_code": "sys.features", "function_name": "功能目錄", "is_active": "true"},
    )
    assert response.status_code == 303
    db.commit.assert_called_once()
    repository.insert_function.assert_called_once()
    repository.write_permission.assert_not_called()


def test_unregistered_catalog_defaults_inactive_but_can_be_enabled(
    browser: TestClient, db: MagicMock
) -> None:
    data = {"function_code": "future.unreviewed", "function_name": "待審查"}
    assert browser.post("/functions", headers=ORIGIN, data=data).status_code == 303
    assert repository.insert_function.call_args.args[1].is_active is False
    db.reset_mock()
    assert (
        browser.post("/functions", headers=ORIGIN, data={**data, "is_active": "true"}).status_code
        == 303
    )
    assert repository.insert_function.call_args.args[1].is_active is True
    db.commit.assert_called_once()
    repository.write_permission.assert_not_called()


@pytest.mark.parametrize(
    "extra",
    [
        {"role": "admin"},
        {"route": "https://evil.invalid"},
        {"function_code": " "},
        {"app_scope": "root"},
    ],
)
def test_function_inputs_reject_unapproved_fields(
    browser: TestClient, db: MagicMock, extra: dict[str, str]
) -> None:
    assert (
        browser.post(
            "/functions",
            headers=ORIGIN,
            data={"function_code": "sys.features", "function_name": "功能", **extra},
        ).status_code
        == 400
    )
    db.commit.assert_not_called()


def test_function_update_revision_immutable_code_and_disable(
    browser: TestClient, db: MagicMock
) -> None:
    data = {
        "function_code": "sys.departments",
        "function_name": "已改名",
        "expected_revision": service.content_revision(FUNCTIONS[0]),
    }
    path = f"/functions/{FIRST}"
    assert (
        browser.post(path, headers=ORIGIN, data={**data, "expected_revision": "0" * 64}).status_code
        == 409
    )
    assert (
        browser.post(
            path, headers=ORIGIN, data={**data, "function_code": "sys.features"}
        ).status_code
        == 400
    )
    assert browser.post(path, headers=ORIGIN, data=data).status_code == 303
    assert repository.update_function.call_args.args[2].is_active is False
    repository.write_permission.assert_not_called()
    db.commit.assert_called_once()


def test_matrix_save_multiple_rows_then_partial_revoke_preserves_omitted(db: MagicMock) -> None:
    service.save_matrix(
        DEPT,
        service.content_revision(MATRIX),
        {FIRST: frozenset({Action.VIEW, Action.EDIT}), SECOND: frozenset({Action.VIEW})},
    )
    assert repository.write_permission.call_count == 2
    db.commit.assert_called_once()
    repository.write_permission.reset_mock()
    service.save_matrix(DEPT, service.content_revision(MATRIX), {FIRST: frozenset()})
    repository.write_permission.assert_called_once_with(db, DEPT, FIRST, frozenset())


def test_matrix_stale_revision_does_not_write(db: MagicMock) -> None:
    with pytest.raises(ConflictError):
        service.save_matrix(DEPT, "0" * 64, {FIRST: frozenset()})
    repository.write_permission.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_called_once()
    db.close.assert_called_once()


@pytest.mark.parametrize(
    "kind",
    [
        "unknown_id",
        "unknown_action",
        "unsupported",
        "disabled_function",
        "disabled_department",
        "frontend_function",
    ],
)
def test_matrix_rejects_unknown_or_inactive_additions(
    db: MagicMock, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    matrix = MATRIX
    grants = {FIRST: frozenset({Action.VIEW, Action.EDIT})}
    if kind == "unknown_id":
        grants = {uuid4(): frozenset({Action.VIEW})}
    elif kind == "unknown_action":
        grants = {FIRST: frozenset({"approve"})}
    elif kind == "unsupported":
        grants = {FIRST: frozenset({Action.DELETE})}
    elif kind == "disabled_function":
        matrix = replace(
            MATRIX, rows=(replace(MATRIX.rows[0], function=replace(FUNCTIONS[0], is_active=False)),)
        )
    elif kind == "frontend_function":
        matrix = replace(
            MATRIX,
            rows=(replace(MATRIX.rows[0], function=replace(FUNCTIONS[0], app_scope="frontend")),),
        )
    else:
        matrix = replace(MATRIX, is_active=False)
    monkeypatch.setattr(repository, "read_matrix", lambda *args, **kwargs: matrix)
    with pytest.raises(EError):
        service.save_matrix(DEPT, service.content_revision(matrix), grants)
    db.commit.assert_not_called()
    repository.write_permission.assert_not_called()


@pytest.mark.parametrize("failure", ["write", "commit"])
def test_matrix_db_failure_rolls_back_and_hides_private_details(
    db: MagicMock, failure: str
) -> None:
    error = OperationalError("synthetic-private-sql", {}, Exception("synthetic-private-host"))
    if failure == "write":
        repository.write_permission.side_effect = error
    else:
        db.commit.side_effect = error
    with pytest.raises(UnavailableError) as caught:
        service.save_matrix(
            DEPT, service.content_revision(MATRIX), {SECOND: frozenset({Action.VIEW})}
        )
    assert "synthetic-" not in str(caught.value)
    db.rollback.assert_called_once()
    db.close.assert_called_once()


def test_duplicate_function_failure_maps_to_409(db: MagicMock) -> None:
    repository.insert_function.side_effect = IntegrityError(
        "synthetic-private", {}, Exception("synthetic-value")
    )
    with pytest.raises(ConflictError):
        service.save_function(FeatureInput(function_code="sys.features", function_name="重複"))
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_output_escapes_database_names(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        repository,
        "page_functions",
        lambda *args: (replace(FUNCTIONS[0], function_name="<script>synthetic()</script>"),),
    )
    page = browser.get("/functions")
    assert page.status_code == 200
    assert "&lt;script&gt;" in page.text and "<script>synthetic()" not in page.text


def test_permission_sql_binds_values_and_never_deletes_rows() -> None:
    db = MagicMock()
    repository.write_permission(db, DEPT, FIRST, frozenset({Action.VIEW}))
    statement, values = db.execute.call_args.args
    assert "ON CONFLICT" in str(statement) and "DELETE FROM" not in str(statement)
    assert values == {
        "department_id": DEPT,
        "function_id": FIRST,
        "can_view": True,
        "can_create": False,
        "can_edit": False,
        "can_delete": False,
    }


def test_repository_locks_are_bounded_and_refuse_restricted_visibility() -> None:
    db = MagicMock()
    db.execute.return_value.scalar_one.return_value = True
    with pytest.raises(UnavailableError):
        repository.prepare_write(db, matrix=True)
    assert all("LOCK TABLE" not in str(call.args[0]) for call in db.execute.call_args_list)
    db.reset_mock()
    db.execute.return_value.scalar_one.return_value = False
    repository.prepare_write(db, matrix=True)
    statements = [str(call.args[0]) for call in db.execute.call_args_list]
    assert statements[0] == "SET LOCAL lock_timeout = '2s'"
    assert statements[-2:] == [
        "LOCK TABLE public.app_functions IN SHARE MODE",
        "LOCK TABLE public.department_permissions IN SHARE ROW EXCLUSIVE MODE",
    ]


@pytest.mark.parametrize("failure", ["rollback", "close"])
def test_session_cleanup_errors_are_sanitized(db: MagicMock, failure: str) -> None:
    getattr(db, failure).side_effect = RuntimeError("synthetic-private-cleanup")
    with pytest.raises(UnavailableError) as caught:
        service.list_functions()
    assert "synthetic-private" not in str(caught.value)


def test_matrix_sql_keeps_an_empty_catalog_department_and_no_cross_department_union() -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value = [
        {
            "department_id": DEPT,
            "department_code": "SYS",
            "department_name": "系統管理",
            "department_active": True,
            "function_id": None,
        }
    ]
    result = repository.read_matrix(db, DEPT)
    assert result.rows == () and result.department_id == DEPT
    statement, values = db.execute.call_args.args
    assert "WHERE d.department_id = :department_id" in str(statement)
    assert "bool_or" not in str(statement)
    assert values == {"department_id": DEPT}


def test_list_is_a_table_without_inline_mutations(browser: TestClient) -> None:
    page = browser.get("/functions")
    assert page.status_code == 200
    assert "<table" in page.text and "新增功能" in page.text
    assert f"/functions/{FIRST}/edit?" in page.text
    # 只檢查列表內容沒有內嵌修改表單，側欄的 POST 登出仍應保留。
    content = re.search(r"<main\b[^>]*>(.*?)</main>", page.text, re.DOTALL)
    assert content is not None
    assert 'method="post"' not in content.group(1)
    assert "每頁 20 筆" in page.text


def test_search_filters_reach_store_and_links_keep_context(browser: TestClient) -> None:
    response = browser.get(
        "/functions", params={"q": " 功能 ", "platform": "admin", "status": "active"}
    )
    assert response.status_code == 200
    query = repository.page_functions.call_args.args[1]
    assert query == FunctionListQuery(q="功能", platform="admin", status="active")
    assert "platform=admin&amp;status=active&amp;page=1" in response.text
    assert "下一頁" not in response.text


@pytest.mark.parametrize(
    "total,requested,expected,pages", [(45, 2, 2, 3), (21, 99, 2, 2), (0, 10, 1, 1)]
)
def test_page_clamps_to_last_result(
    db: MagicMock, total: int, requested: int, expected: int, pages: int
) -> None:
    repository.count_functions.return_value = total
    result = service.search_functions(FunctionListQuery(page=requested))
    assert result.total == total and result.pages == pages and result.query.page == expected
    assert repository.page_functions.call_args.args[1].page == expected
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "params",
    [
        {"page": "0"},
        {"page": "-1"},
        {"page": "1000001"},
        {"page": "bad"},
        {"platform": "root"},
        {"status": "deleted"},
        {"q": "x" * 201},
        {"q": "a\x00b"},
    ],
)
def test_invalid_list_query_cannot_reach_database(
    browser: TestClient, params: dict[str, str]
) -> None:
    assert browser.get("/functions", params=params).status_code == 400
    repository.count_functions.assert_not_called()


@pytest.mark.parametrize("filtered", [False, True])
def test_empty_list_distinguishes_no_data_and_no_matches(
    browser: TestClient, filtered: bool
) -> None:
    repository.count_functions.return_value = 0
    repository.page_functions.return_value = ()
    page = browser.get("/functions?q=missing" if filtered else "/functions")
    assert page.status_code == 200
    assert ("沒有符合條件的功能" if filtered else "尚未建立功能") in page.text
    assert "共 0 筆" in page.text


def test_read_failure_is_not_reported_as_an_empty_list(browser: TestClient) -> None:
    repository.count_functions.side_effect = RuntimeError("synthetic-private-db")
    page = browser.get("/functions?q=test")
    assert page.status_code == 503
    assert "重新載入" in page.text and "尚未建立功能" not in page.text
    assert "synthetic-private" not in page.text


def test_new_and_edit_content_pages_keep_return_filters(browser: TestClient, db: MagicMock) -> None:
    for path in ["/functions/new", f"/functions/{FIRST}/edit"]:
        page = browser.get(
            path, params={"q": "財務", "platform": "both", "status": "inactive", "page": "3"}
        )
        assert page.status_code == 200
        assert 'name="return_q" value="財務"' in page.text
        assert 'name="return_page" value="3"' in page.text
        assert "platform=both&amp;status=inactive&amp;page=3" in page.text
        assert page.headers["cache-control"] == "no-store" and "set-cookie" not in page.headers
    assert "readonly" in page.text and "停用功能" in page.text
    assert re.search(r'name="is_active"\s+value="true"', page.text)
    db.commit.assert_not_called()


def test_missing_content_and_status_do_not_write(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch, db: MagicMock
) -> None:
    monkeypatch.setattr(repository, "get_function", MagicMock(side_effect=MissingError()))
    assert browser.get(f"/functions/{FIRST}/edit").status_code == 404
    assert (
        browser.post(
            f"/functions/{FIRST}/status",
            headers=ORIGIN,
            data={"is_active": "false", "expected_revision": "0" * 64},
        ).status_code
        == 404
    )
    repository.update_function.assert_not_called()
    db.commit.assert_not_called()


def test_validation_preserves_safe_inputs_without_echoing_extra_fields(browser: TestClient) -> None:
    page = browser.post(
        "/functions",
        headers=ORIGIN,
        data={
            "function_code": "future.draft",
            "function_name": "測試 <img>",
            "role": "synthetic-private-role",
            "return_q": "財務",
        },
    )
    assert page.status_code == 400
    assert 'value="future.draft"' in page.text and "測試 &lt;img&gt;" in page.text
    assert 'name="return_q" value="財務"' in page.text
    assert "synthetic-private-role" not in page.text
    repository.insert_function.assert_not_called()


def test_duplicate_create_keeps_editable_form(browser: TestClient) -> None:
    repository.insert_function.side_effect = IntegrityError("synthetic-private", {}, Exception())
    page = browser.post(
        "/functions",
        headers=ORIGIN,
        data={"function_code": "sys.features", "function_name": "重複名稱"},
    )
    assert page.status_code == 409 and 'value="重複名稱"' in page.text
    assert "disabled" not in page.text


def test_stale_edit_keeps_original_revision_and_requires_reload(
    browser: TestClient, db: MagicMock
) -> None:
    page = browser.post(
        f"/functions/{FIRST}",
        headers=ORIGIN,
        data={
            "function_code": "sys.departments",
            "function_name": "尚未儲存的名稱",
            "is_active": "true",
            "expected_revision": "0" * 64,
        },
    )
    assert page.status_code == 409
    assert "尚未儲存的名稱" in page.text and "重新載入目前資料" in page.text
    assert 'value="' + "0" * 64 + '"' in page.text
    assert "disabled" in page.text
    repository.update_function.assert_not_called()
    db.commit.assert_not_called()


def test_save_keeps_current_status_and_redirects_only_to_fixed_list(browser: TestClient) -> None:
    response = browser.post(
        f"/functions/{FIRST}",
        headers=ORIGIN,
        data={
            "function_code": "sys.departments",
            "function_name": "修改後名稱",
            "is_active": "true",
            "expected_revision": service.content_revision(FUNCTIONS[0]),
            "return_q": "https://example.invalid/a",
            "return_platform": "admin",
            "return_status": "active",
            "return_page": "2",
        },
    )
    assert response.status_code == 303
    url = urlsplit(response.headers["location"])
    assert url.path == "/functions" and not url.netloc
    assert parse_qs(url.query) == {
        "q": ["https://example.invalid/a"],
        "platform": ["admin"],
        "status": ["active"],
        "page": ["2"],
        "saved": ["true"],
    }
    assert repository.update_function.call_args.args[2].is_active is True


@pytest.mark.parametrize("target_active", [False, True])
def test_status_change_preserves_metadata_and_grants(
    browser: TestClient, db: MagicMock, monkeypatch: pytest.MonkeyPatch, target_active: bool
) -> None:
    current = replace(FUNCTIONS[0], is_active=not target_active)
    monkeypatch.setattr(repository, "get_function", lambda *args, **kwargs: current)
    page = browser.post(
        f"/functions/{FIRST}/status",
        headers=ORIGIN,
        data={
            "is_active": str(target_active).lower(),
            "expected_revision": service.content_revision(current),
        },
    )
    assert page.status_code == 303
    data = repository.update_function.call_args.args[2]
    assert (
        data.function_code == current.function_code and data.function_name == current.function_name
    )
    assert data.app_scope == current.app_scope and data.is_active is target_active
    repository.write_permission.assert_not_called()
    db.commit.assert_called_once()


def test_status_stale_revision_does_not_write(browser: TestClient, db: MagicMock) -> None:
    page = browser.post(
        f"/functions/{FIRST}/status",
        headers=ORIGIN,
        data={"is_active": "false", "expected_revision": "0" * 64},
    )
    assert page.status_code == 409 and "重新載入目前資料" in page.text
    repository.update_function.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "extra",
    [
        {"role": "admin"},
        {"function_name": "不可改"},
        {"is_active": "bad"},
        {"next": "https://example.invalid"},
        {"return_page": "0"},
    ],
)
def test_status_form_rejects_unapproved_fields(
    browser: TestClient, db: MagicMock, extra: dict[str, str]
) -> None:
    page = browser.post(
        f"/functions/{FIRST}/status",
        headers=ORIGIN,
        data={
            "is_active": "false",
            "expected_revision": service.content_revision(FUNCTIONS[0]),
            **extra,
        },
    )
    assert page.status_code == 400
    db.commit.assert_not_called()


def test_status_requires_origin_and_does_not_allow_hard_delete(
    browser: TestClient, db: MagicMock
) -> None:
    assert (
        browser.post(f"/functions/{FIRST}/status", data={"is_active": "false"}).status_code == 403
    )
    assert browser.delete(f"/functions/{FIRST}", headers=ORIGIN).status_code == 405
    db.commit.assert_not_called()


def test_unregistered_catalog_can_be_enabled_without_granting_departments(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch, db: MagicMock
) -> None:
    current = replace(FUNCTIONS[0], function_code="future.draft", is_active=False)
    monkeypatch.setattr(repository, "get_function", lambda *args, **kwargs: current)
    page = browser.post(
        f"/functions/{FIRST}/status",
        headers=ORIGIN,
        data={"is_active": "true", "expected_revision": service.content_revision(current)},
    )
    assert page.status_code == 303
    assert repository.update_function.call_args.args[2].is_active is True
    repository.write_permission.assert_not_called()
    db.commit.assert_called_once()


@pytest.mark.parametrize(
    "path,method",
    [
        ("/functions/new", "get"),
        (f"/functions/{FIRST}/edit", "get"),
        (f"/functions/{FIRST}/status", "post"),
    ],
)
def test_new_routes_stay_closed_in_cloud(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, method: str
) -> None:
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert getattr(browser, method)(path, headers=ORIGIN).status_code == 404


def test_repository_search_is_bounded_and_parameterized() -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value = []
    query = FunctionListQuery(q="%_'; SELECT synthetic", platform="both", status="inactive", page=3)
    assert repository.page_functions(db, query) == ()
    statement, values = db.execute.call_args.args
    assert query.q not in str(statement)
    assert "strpos(" in str(statement) and "LIMIT :limit OFFSET :offset" in str(statement)
    assert values == {
        "q": query.q,
        "platform": "both",
        "status": "inactive",
        "limit": 20,
        "offset": 40,
    }


def test_read_content_does_not_lock_or_commit() -> None:
    db = MagicMock()
    db.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "function_id": FIRST,
        "function_code": "sys.features",
        "function_name": "功能",
        "app_scope": "admin",
        "is_active": True,
    }
    repository.get_function(db, FIRST, lock=False)
    assert "FOR UPDATE" not in str(db.execute.call_args.args[0])
    db.commit.assert_not_called()


@pytest.mark.parametrize("failure", ["write", "commit"])
def test_function_write_failure_keeps_input_and_hides_database_details(
    browser: TestClient, db: MagicMock, failure: str
) -> None:
    error = OperationalError("synthetic-private-query", {}, Exception("synthetic-private-host"))
    if failure == "write":
        repository.update_function.side_effect = error
    else:
        db.commit.side_effect = error
    page = browser.post(
        f"/functions/{FIRST}",
        headers=ORIGIN,
        data={
            "function_code": "sys.departments",
            "function_name": "待確認的名稱",
            "is_active": "true",
            "expected_revision": service.content_revision(FUNCTIONS[0]),
        },
    )
    assert page.status_code == 503
    assert "待確認的名稱" in page.text and "重新載入目前資料" in page.text
    assert "synthetic-private" not in page.text
    db.rollback.assert_called_once()
    db.close.assert_called_once()


def test_frontend_catalog_can_be_enabled_without_admin_permission(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch, db: MagicMock
) -> None:
    current = replace(FUNCTIONS[0], app_scope="frontend", is_active=False)
    monkeypatch.setattr(repository, "get_function", lambda *args, **kwargs: current)
    page = browser.post(
        f"/functions/{FIRST}/status",
        headers=ORIGIN,
        data={"is_active": "true", "expected_revision": service.content_revision(current)},
    )
    assert page.status_code == 303
    assert repository.update_function.call_args.args[2].app_scope == "frontend"
    assert repository.update_function.call_args.args[2].is_active is True
    repository.write_permission.assert_not_called()
    db.commit.assert_called_once()


def test_duplicate_form_keys_are_rejected(browser: TestClient, db: MagicMock) -> None:
    page = browser.post(
        "/functions",
        headers={**ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
        content="function_code=sys.features&function_code=sys.departments&function_name=test",
    )
    assert page.status_code == 400
    db.commit.assert_not_called()


@pytest.mark.parametrize("platform", ["admin", "frontend", "both"])
@pytest.mark.parametrize("enabled", [True, False])
def test_accounts_catalog_status_round_trip_preserves_fields_and_never_writes_grants(
    browser: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    db: MagicMock,
    platform: str,
    enabled: bool,
) -> None:
    """重現使用者 accounts 代碼，不要求命名成 sys.* 或修改正式 registry。"""
    current = replace(
        FUNCTIONS[0],
        function_code="accounts",
        function_name="帳號管理",
        app_scope=platform,
        is_active=not enabled,
    )
    monkeypatch.setattr(repository, "get_function", lambda *args, **kwargs: current)
    page = browser.get(f"/functions/{FIRST}/edit")
    assert page.status_code == 200 and "待接線" in page.text
    response = browser.post(
        f"/functions/{FIRST}/status",
        headers=ORIGIN,
        data={
            "is_active": str(enabled).lower(),
            "expected_revision": service.content_revision(current),
        },
    )
    assert response.status_code == 303
    saved = repository.update_function.call_args.args[2]
    assert (saved.function_code, saved.function_name, saved.app_scope, saved.is_active) == (
        "accounts",
        "帳號管理",
        platform,
        enabled,
    )
    db.commit.assert_called_once()
    repository.write_permission.assert_not_called()


def test_active_unregistered_catalog_still_cannot_be_a_formal_capability(
    browser: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.schemas.e_admin import DeniedError
    from app.services.e_registry import capability, supported_actions

    current = replace(FUNCTIONS[0], function_code="accounts", is_active=True)
    monkeypatch.setattr(repository, "page_functions", lambda *args: (current,))
    page = browser.get("/functions")
    assert "目錄狀態" in page.text
    assert "程式對應" not in page.text
    content = re.search(r"<main\b[^>]*>(.*?)</main>", page.text, re.DOTALL)
    assert content is not None and "待接線" not in content.group(1)
    assert supported_actions("accounts") == frozenset()
    with pytest.raises(DeniedError):
        capability("accounts")


@pytest.mark.parametrize("enabled", [True, False])
def test_builtin_welcome_cannot_be_disabled_or_changed(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch, db: MagicMock, enabled: bool
) -> None:
    current = replace(
        FUNCTIONS[0], function_code="welcome", function_name="歡迎頁", is_active=enabled
    )
    monkeypatch.setattr(repository, "get_function", lambda *args, **kwargs: current)
    page = browser.get(f"/functions/{FIRST}/edit")
    assert "系統內建" in page.text
    assert f'action="/functions/{FIRST}/status"' not in page.text
    response = browser.post(
        f"/functions/{FIRST}/status",
        headers=ORIGIN,
        data={
            "is_active": str(not enabled).lower(),
            "expected_revision": service.content_revision(current),
        },
    )
    assert response.status_code == 400 and "歡迎頁固定可查看" in response.text
    update = browser.post(
        f"/functions/{FIRST}",
        headers=ORIGIN,
        data={
            "function_code": "welcome",
            "function_name": "更改首頁",
            "is_active": "true",
            "expected_revision": service.content_revision(current),
        },
    )
    assert update.status_code == 400
    repository.update_function.assert_not_called()
    db.commit.assert_not_called()


def test_welcome_is_builtin_without_creating_a_catalog_seed(
    browser: TestClient, db: MagicMock
) -> None:
    response = browser.post(
        "/functions",
        headers=ORIGIN,
        data={"function_code": "welcome", "function_name": "歡迎頁", "is_active": "true"},
    )
    assert response.status_code == 400 and "歡迎頁固定可查看" in response.text
    repository.insert_function.assert_not_called()
