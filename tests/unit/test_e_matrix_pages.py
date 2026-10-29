"""原獨立矩陣頁已移除；以部門內容頁驗證同一表單、原子交易及舊入口退場。"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from html.parser import HTMLParser
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import settings
from app.db.repositories import department_repository as departments
from app.db.repositories import e_function_settings_repository as permissions
from app.main import app
from app.routers import e_admin
from app.schemas.department import Department
from app.schemas.department_detail import DepartmentDetail
from app.schemas.e_admin import Action
from app.schemas.e_function_settings import (
    DepartmentFunctionSettings,
    FunctionPermission,
    FunctionSetting,
)
from app.services import department_detail_service as service

BASE = "http://invoiceguard-ai-admin.localhost:8080"
ORIGIN = {"Origin": BASE}
DEPT = UUID("00000000-0000-4000-8000-000000000010")
ONE = UUID("00000000-0000-4000-8000-000000000011")
TWO = UUID("00000000-0000-4000-8000-000000000012")
ROW = Department(
    department_id=DEPT,
    department_code="SYNTHETIC",
    department_name="虛構部門",
    is_active=True,
    created_at=datetime.now(UTC),
    updated_at=datetime.now(UTC),
)
MATRIX = DepartmentFunctionSettings(
    DEPT,
    ROW.department_code,
    ROW.department_name,
    True,
    (
        FunctionPermission(
            FunctionSetting(ONE, "sys.features", "功能目錄", "admin", True),
            frozenset({Action.VIEW}),
        ),
        FunctionPermission(
            FunctionSetting(TWO, "future.synthetic", "待接線功能", "admin", False), frozenset()
        ),
    ),
)
PATH = f"/department/{DEPT}/edit"


class Form(HTMLParser):
    """擷取實際內容表單成功控制項，模擬瀏覽器送出勾選結果。"""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.values: dict[str, list[str]] = {}
        self.active = False
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self.active = values.get("action") == PATH
        if (
            tag == "input"
            and self.active
            and values.get("name")
            and "disabled" not in values
            and (values.get("type") != "checkbox" or "checked" in values)
        ):
            self.values.setdefault(str(values["name"]), []).append(values.get("value") or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.active = False


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """保留服務交易流程，所有 DB 邊界使用合成替身。"""
    session = MagicMock()
    monkeypatch.setattr(permissions, "open_session", lambda: session)
    monkeypatch.setattr(permissions, "prepare_write", MagicMock())
    monkeypatch.setattr(permissions, "read_matrix", MagicMock(return_value=MATRIX))
    monkeypatch.setattr(permissions, "write_permission", MagicMock())
    monkeypatch.setattr(departments, "get_department", MagicMock(return_value=ROW))
    monkeypatch.setattr(departments, "lock_department", MagicMock(return_value=ROW))
    monkeypatch.setattr(departments, "update_details", MagicMock())
    monkeypatch.setattr(departments, "code_change_blocker", MagicMock(return_value=None))
    monkeypatch.setattr(departments, "disable_blocker", MagicMock(return_value=None))
    return session


@pytest.fixture
def browser(db: MagicMock, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """真實本機配置與同源 guard；不覆寫登入／授權相依。"""
    monkeypatch.setattr(e_admin, "LOCAL_SETUP_ENABLED", True)
    with TestClient(
        app, base_url=BASE, client=("127.0.0.1", 50000), follow_redirects=False
    ) as client:
        yield client


def payload() -> dict[str, object]:
    """完整整頁配置；四項操作互不隱含。"""
    return {
        "department_code": ROW.department_code,
        "department_name": "新名稱",
        "is_active": "true",
        "expected_revision": service.revision(DepartmentDetail(ROW, MATRIX)),
        "catalog_complete": "true",
        "function_id": [str(ONE), str(TWO)],
        "grant": [f"{ONE}:view", f"{TWO}:create"],
    }


def test_html_round_trip_saves_metadata_and_all_four_independent_actions(
    browser: TestClient, db: MagicMock
) -> None:
    page = browser.get(PATH)
    assert page.status_code == 200 and "授權功能區" in page.text
    assert "待接線" in page.text and "尚無功能" not in page.text
    assert 'href="/department-functions"' not in page.text
    form = Form(page.text).values
    assert form["grant"] == [f"{ONE}:view"]
    assert set(form["function_id"]) == {str(ONE), str(TWO)}
    for action in Action:
        assert f'value="{TWO}:{action.value}"' in page.text
    form["department_name"] = ["新名稱"]
    form["grant"] = [f"{ONE}:view", f"{TWO}:create", f"{TWO}:delete"]
    response = browser.post(PATH, data=form, headers=ORIGIN)
    assert response.status_code == 303 and response.headers["location"] == PATH + "?saved=true"
    departments.update_details.assert_called_once_with(
        db, DEPT, ROW.department_code, "新名稱", True
    )
    permissions.write_permission.assert_called_once_with(
        db, DEPT, TWO, frozenset({Action.CREATE, Action.DELETE})
    )
    db.commit.assert_called_once()


def test_all_unchecked_explicitly_revokes_without_hard_delete(
    browser: TestClient, db: MagicMock
) -> None:
    data = payload()
    data.pop("grant")
    assert browser.post(PATH, data=data, headers=ORIGIN).status_code == 303
    permissions.write_permission.assert_called_once_with(db, DEPT, ONE, frozenset())


@pytest.mark.parametrize("change", ["department", "grant", "catalog"])
def test_concurrent_changes_block_both_metadata_and_grants(
    browser: TestClient, db: MagicMock, change: str
) -> None:
    data = payload()
    if change == "department":
        departments.lock_department.return_value = ROW.model_copy(
            update={"department_name": "其他人已更新"}
        )
    elif change == "grant":
        permissions.read_matrix.return_value = replace(
            MATRIX, rows=(replace(MATRIX.rows[0], actions=frozenset({Action.EDIT})), MATRIX.rows[1])
        )
    else:
        permissions.read_matrix.return_value = replace(MATRIX, rows=MATRIX.rows[:1])
    response = browser.post(PATH, data=data, headers=ORIGIN)
    assert response.status_code == 409 and "disabled" in response.text
    assert Form(response.text).values["expected_revision"] == [data["expected_revision"]]
    departments.update_details.assert_not_called()
    permissions.write_permission.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "fault",
    [
        "foreign_id",
        "omitted_id",
        "duplicate_id",
        "unknown_action",
        "duplicate_grant",
        "extra_role",
        "missing_catalog",
        "duplicate_name",
        "wrong_target",
    ],
)
def test_tampered_payload_never_writes(browser: TestClient, db: MagicMock, fault: str) -> None:
    data = payload()
    if fault == "foreign_id":
        data["function_id"] = [str(ONE), str(DEPT)]
    if fault == "omitted_id":
        data["function_id"] = [str(ONE)]
        data["grant"] = [f"{ONE}:view"]
    if fault == "duplicate_id":
        data["function_id"] = [str(ONE), str(ONE)]
    if fault == "unknown_action":
        data["grant"] = [f"{ONE}:approve"]
    if fault == "duplicate_grant":
        data["grant"] = [f"{ONE}:view", f"{ONE}:view"]
    if fault == "extra_role":
        data["role"] = "admin"
    if fault == "missing_catalog":
        data.pop("catalog_complete")
    if fault == "duplicate_name":
        data["department_name"] = ["A", "B"]
    if fault == "wrong_target":
        data["department_id"] = str(TWO)
    assert browser.post(PATH, data=data, headers=ORIGIN).status_code == 400
    departments.update_details.assert_not_called()
    permissions.write_permission.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": "https://untrusted.invalid"}, {"Origin": BASE, "Sec-Fetch-Site": "cross-site"}],
)
def test_write_requires_origin_before_any_read(
    browser: TestClient, db: MagicMock, headers: dict[str, str]
) -> None:
    assert browser.post(PATH, data=payload(), headers=headers).status_code == 403
    departments.get_department.assert_not_called()
    departments.lock_department.assert_not_called()


@pytest.mark.parametrize("fault", ["metadata", "grant", "commit", "duplicate_code"])
def test_any_failure_rolls_back_same_transaction_and_hides_sql(
    browser: TestClient, db: MagicMock, fault: str
) -> None:
    error = OperationalError("synthetic-private-sql", {}, Exception("synthetic-private-host"))
    target = {
        "metadata": departments.update_details,
        "grant": permissions.write_permission,
        "commit": db.commit,
        "duplicate_code": departments.update_details,
    }[fault]
    target.side_effect = (
        IntegrityError("synthetic-private-sql", {}, Exception("synthetic-private-value"))
        if fault == "duplicate_code"
        else error
    )
    response = browser.post(PATH, data=payload(), headers=ORIGIN)
    assert response.status_code == (409 if fault == "duplicate_code" else 503)
    assert "synthetic-private" not in response.text
    assert db.rollback.called and db.close.called
    if fault != "commit":
        db.commit.assert_not_called()


def test_validation_preserves_name_checks_and_original_revision(browser: TestClient) -> None:
    data = payload()
    data["department_name"] = " "
    page = browser.post(PATH, data=data, headers=ORIGIN)
    assert page.status_code == 400
    form = Form(page.text).values
    assert form["department_name"] == [" "]
    assert form["grant"] == data["grant"]
    assert form["expected_revision"] == [data["expected_revision"]]


def test_empty_catalog_still_allows_department_metadata(browser: TestClient, db: MagicMock) -> None:
    empty = replace(MATRIX, rows=())
    permissions.read_matrix.return_value = empty
    page = browser.get(PATH)
    assert "尚無其他功能資料" in page.text
    data = Form(page.text).values
    data["department_name"] = ["空目錄也可修改"]
    assert browser.post(PATH, data=data, headers=ORIGIN).status_code == 303
    permissions.write_permission.assert_not_called()
    db.commit.assert_called_once()


def test_read_uses_consistent_snapshot_without_write_locks(db: MagicMock) -> None:
    service.read_detail(DEPT)
    sql = str(db.execute.call_args_list[0].args[0])
    assert "REPEATABLE READ, READ ONLY" in sql
    departments.lock_department.assert_not_called()
    permissions.write_permission.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_called_once()


@pytest.mark.parametrize("kind", ["metadata", "grant", "directory"])
def test_joint_revision_covers_all_resources(kind: str) -> None:
    current = DepartmentDetail(ROW, MATRIX)
    changed = current
    if kind == "metadata":
        changed = replace(current, department=ROW.model_copy(update={"is_active": False}))
    if kind == "grant":
        changed = replace(
            current,
            matrix=replace(
                MATRIX, rows=(replace(MATRIX.rows[0], actions=frozenset()), MATRIX.rows[1])
            ),
        )
    if kind == "directory":
        changed = replace(
            current,
            matrix=replace(
                MATRIX,
                rows=(
                    replace(
                        MATRIX.rows[0],
                        function=replace(MATRIX.rows[0].function, function_name="更名"),
                    ),
                    MATRIX.rows[1],
                ),
            ),
        )
    assert service.revision(current) != service.revision(changed)


@pytest.mark.parametrize("kind", ["reserved", "code_reference", "primary_department"])
def test_existing_department_protections_also_block_permission_changes(
    browser: TestClient, db: MagicMock, kind: str
) -> None:
    data = payload()
    if kind == "reserved":
        data["department_code"] = "UNASSIGNED"
    if kind == "code_reference":
        data["department_code"] = "NEW"
        departments.code_change_blocker.return_value = "referenced"
    if kind == "primary_department":
        data["is_active"] = "false"
        departments.disable_blocker.return_value = "primary"
    assert browser.post(PATH, data=data, headers=ORIGIN).status_code == 400
    departments.update_details.assert_not_called()
    permissions.write_permission.assert_not_called()
    db.commit.assert_not_called()


def test_missing_department_is_404(browser: TestClient, db: MagicMock) -> None:
    departments.get_department.return_value = None
    departments.lock_department.return_value = None
    assert browser.get(PATH).status_code == 404
    assert browser.post(PATH, data=payload(), headers=ORIGIN).status_code == 404


def test_database_text_is_escaped(browser: TestClient) -> None:
    departments.get_department.return_value = ROW.model_copy(
        update={"department_name": "<script>synthetic()</script>"}
    )
    permissions.read_matrix.return_value = replace(
        MATRIX,
        rows=(
            replace(
                MATRIX.rows[0],
                function=replace(
                    MATRIX.rows[0].function, function_name="<script>synthetic()</script>"
                ),
            ),
        ),
    )
    page = browser.get(PATH)
    assert "&lt;script&gt;synthetic()" in page.text and "<script>synthetic()" not in page.text


@pytest.mark.parametrize("boundary", ["cloud", "host", "peer"])
def test_new_detail_cannot_open_outside_local(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    base, peer = BASE, "127.0.0.1"
    if boundary == "cloud":
        monkeypatch.setattr(settings, "APP_ENV", "production")
    if boundary == "host":
        base = "http://evil.invalid"
    if boundary == "peer":
        peer = "192.0.2.1"
    with TestClient(app, base_url=base, client=(peer, 50000), follow_redirects=False) as client:
        assert client.get(PATH).status_code == 404
        assert client.post(PATH, data=payload(), headers=ORIGIN).status_code == 404
    departments.get_department.assert_not_called()
    departments.update_details.assert_not_called()


def test_disabled_setup_requires_login_before_detail_repository(
    browser: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(e_admin, "LOCAL_SETUP_ENABLED", False)
    assert browser.get(PATH).status_code == 303
    assert browser.post(PATH, data=payload(), headers=ORIGIN).status_code == 303
    departments.get_department.assert_not_called()
    departments.lock_department.assert_not_called()


def test_old_get_is_only_a_fixed_redirect(browser: TestClient) -> None:
    for suffix, target in [("", "/department"), (f"?department_id={DEPT}", PATH)]:
        response = browser.get("/department-functions" + suffix)
        assert response.status_code == 303 and response.headers["location"] == target
    permissions.read_matrix.assert_not_called()


@pytest.mark.parametrize("stage", ["", "/preview", "/draft"])
def test_old_write_forms_are_gone_and_never_replayed(
    browser: TestClient, db: MagicMock, stage: str
) -> None:
    response = browser.post(f"/department-functions/{DEPT}" + stage, data=payload(), headers=ORIGIN)
    assert response.status_code == 410
    permissions.read_matrix.assert_not_called()
    permissions.write_permission.assert_not_called()
    db.commit.assert_not_called()


def test_new_department_without_welcome_catalog_record_has_fixed_view(
    browser: TestClient, db: MagicMock
) -> None:
    permissions.read_matrix.return_value = replace(MATRIX, rows=())
    page = browser.get(PATH)
    assert page.status_code == 200
    assert page.text.count("data-builtin-welcome") == 1
    assert 'checked disabled aria-label="歡迎頁：固定可查看"' in page.text
    for action in ["新增", "修改", "刪除"]:
        assert f'disabled aria-label="歡迎頁：不可{action}"' in page.text
    assert "grant" not in Form(page.text).values
    permissions.write_permission.assert_not_called()


def test_existing_welcome_row_is_normalized_only_on_user_save(
    browser: TestClient, db: MagicMock
) -> None:
    welcome = replace(
        MATRIX.rows[1],
        function=replace(MATRIX.rows[1].function, function_code="welcome", function_name="歡迎頁"),
        actions=frozenset({Action.EDIT, Action.DELETE}),
    )
    permissions.read_matrix.return_value = replace(MATRIX, rows=(MATRIX.rows[0], welcome))
    page = browser.get(PATH)
    assert page.text.count("data-builtin-welcome") == 1
    values = Form(page.text).values
    assert f"{TWO}:view" in values["grant"]
    assert f"{TWO}:edit" not in values["grant"]
    assert browser.post(PATH, data=values, headers=ORIGIN).status_code == 303
    permissions.write_permission.assert_called_once_with(db, DEPT, TWO, frozenset({Action.VIEW}))


@pytest.mark.parametrize("actions", [[], ["view", "edit"], ["create"], ["delete"]])
def test_forged_welcome_flags_cannot_clear_view_or_add_writes(
    browser: TestClient, db: MagicMock, actions: list[str]
) -> None:
    welcome = replace(
        MATRIX.rows[1], function=replace(MATRIX.rows[1].function, function_code="welcome")
    )
    permissions.read_matrix.return_value = replace(MATRIX, rows=(MATRIX.rows[0], welcome))
    data = Form(browser.get(PATH).text).values
    data["grant"] = [f"{ONE}:view"] + [f"{TWO}:{action}" for action in actions]
    assert browser.post(PATH, data=data, headers=ORIGIN).status_code == 400
    permissions.write_permission.assert_not_called()
    departments.update_details.assert_not_called()
    db.commit.assert_not_called()


def test_bulk_controls_are_local_only_and_not_successful_form_fields(
    browser: TestClient, db: MagicMock
) -> None:
    page = browser.get(PATH)
    assert 'data-bulk-value="true"' in page.text and 'data-bulk-value="false"' in page.text
    assert 'type="button"' in page.text
    assert (
        page.text.index("功能／平台／狀態</th>")
        < page.text.index("全選</th>")
        < page.text.index("查看</th>")
    )
    assert "e-department-bulk.js" in page.text
    assert page.text.count("data-row-toggle") == len(MATRIX.rows)
    keys = set(Form(page.text).values)
    assert keys == {
        "department_code",
        "department_name",
        "is_active",
        "expected_revision",
        "catalog_complete",
        "function_id",
        "grant",
    }
    permissions.write_permission.assert_not_called()
    departments.update_details.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("blocked", [False, True])
def test_unassigned_detail_status_uses_shared_rules_and_preserves_grants(
    browser: TestClient, db: MagicMock, blocked: bool
) -> None:
    """同頁停用預設部門不另擋代碼；主要部門限制仍阻擋整筆交易。"""
    current = ROW.model_copy(update={"department_code": "UNASSIGNED"})
    departments.get_department.return_value = current
    departments.lock_department.return_value = current
    departments.disable_blocker.return_value = "主要部門" if blocked else None
    data = Form(browser.get(PATH).text).values
    data["is_active"] = ["false"]
    response = browser.post(PATH, data=data, headers=ORIGIN)
    departments.disable_blocker.assert_called_once_with(db, DEPT)
    permissions.write_permission.assert_not_called()
    if blocked:
        assert response.status_code == 400
        departments.update_details.assert_not_called()
        db.commit.assert_not_called()
        assert db.rollback.called
    else:
        assert response.status_code == 303
        departments.update_details.assert_called_once_with(
            db, DEPT, current.department_code, current.department_name, False
        )
        db.commit.assert_called_once()
