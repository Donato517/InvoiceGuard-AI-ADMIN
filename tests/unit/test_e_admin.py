"""E 獨立驗收：只用合成 UUID、測試替身與封鎖外部服務的既有 fixture。"""

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.constants import SESSION_COOKIE_NAME
from app.main import app
from app.schemas.e_admin import (
    Action,
    ChangeEvent,
    ConflictError,
    DataScope,
    Decision,
    DeniedError,
    Department,
    DepartmentImpact,
    DepartmentInput,
    DepartmentInUseError,
    EError,
    Feature,
    FeatureInput,
    GrantRow,
    Identity,
    IssuedSession,
    Matrix,
    MatrixChange,
    MissingError,
    Profile,
    ProfilePatch,
    Target,
    UnauthenticatedError,
    UnavailableError,
)
from app.services.e_admin_service import DEPARTMENTS, FEATURES, EModule, get_e_module
from app.services.e_authorization import require_permission, verified_identity
from app.services.e_registry import Capability, capability

ACTOR = UUID("00000000-0000-4000-8000-000000000001")
DEPT = UUID("00000000-0000-4000-8000-000000000002")
OTHER_DEPT = UUID("00000000-0000-4000-8000-000000000003")
SESSION = UUID("00000000-0000-4000-8000-000000000004")
TOKEN = "synthetic-session-only-for-offline-tests"
ORIGIN = {"Origin": "http://testserver"}


def identity() -> Identity:
    return Identity(
        ACTOR, SESSION, DEPT, 1, datetime.now(UTC) + timedelta(hours=1), True, "admin", True
    )


class Access:
    """測試專用，正式程式不得匯入。"""

    def __init__(self) -> None:
        self.user: Identity | None = identity()
        self.effects: dict[tuple[str, Action], str] = {}
        self.scope = DataScope("company")
        self.active = True
        self.mismatch = False

    def authenticate(self, token: SecretStr) -> Identity | None:
        return self.user if token.get_secret_value() == TOKEN else None

    def decide(self, user: Identity, code: str, action: Action) -> Decision:
        return Decision(
            uuid4() if self.mismatch else user.user_id,
            user.session_id,
            user.selected_department_id,
            code,
            action,
            self.effects.get((code, action), "allow"),
            self.active,
            "test-policy-1",
            datetime.now(UTC) + timedelta(minutes=2),
            self.scope,
        )


class Auth:
    def __init__(self) -> None:
        self.revoked = False
        self.reset_requests = 0
        self.patch: ProfilePatch | None = None
        self.changed = False

    def login(self, email: str, password: SecretStr) -> IssuedSession:
        return IssuedSession(ACTOR, SecretStr(TOKEN))

    def logout(self, token: SecretStr) -> None:
        self.revoked = True

    def profile(self, user: Identity) -> Profile:
        return Profile(user.user_id, "測試本人", "synthetic@example.invalid")

    def update_profile(self, user: Identity, patch: ProfilePatch) -> None:
        assert user.user_id == ACTOR
        self.patch = patch

    def request_password_reset(self, email: str) -> None:
        self.reset_requests += 1

    def change_password(self, user: Identity, current: SecretStr, new: SecretStr) -> None:
        self.changed = True


class Grants:
    def __init__(self) -> None:
        self.matrix = Matrix(
            "g-1", (), (GrantRow(function_code=DEPARTMENTS, actions=frozenset({Action.VIEW})),)
        )
        self.calls = 0

    def read(self, user: Identity, department_id: UUID) -> Matrix:
        return self.matrix

    def replace(self, user: Identity, department_id: UUID, change: MatrixChange) -> None:
        self.calls += 1
        self.matrix = Matrix("g-2", change.rows, self.matrix.delegable)


@dataclass
class State:
    departments: dict[UUID, Department] = field(default_factory=dict)
    features: dict[UUID, Feature] = field(default_factory=dict)
    events: list[ChangeEvent] = field(default_factory=list)


class Departments:
    def __init__(self, state: State) -> None:
        self.state = state
        self.impact_value = DepartmentImpact()

    def list(self, scope: DataScope) -> list[Department]:
        return [
            row
            for row in self.state.departments.values()
            if scope.covers(ACTOR, Target(row.department_id))
        ]

    def get(self, department_id: UUID, scope: DataScope, *, lock: bool = False) -> Department:
        if department_id not in self.state.departments or not scope.covers(
            ACTOR, Target(department_id)
        ):
            raise MissingError
        return self.state.departments[department_id]

    def insert(self, data: DepartmentInput) -> Department:
        if any(
            row.department_code == data.department_code for row in self.state.departments.values()
        ):
            raise ConflictError
        row = Department(uuid4(), **data.model_dump(), revision="new")
        self.state.departments[row.department_id] = row
        return row

    def update(self, department_id: UUID, data: DepartmentInput) -> Department:
        old = self.state.departments[department_id]
        row = replace(old, **data.model_dump())
        self.state.departments[department_id] = row
        return row

    def impact(self, department_id: UUID) -> DepartmentImpact:
        return self.impact_value


class Features:
    def __init__(self, state: State) -> None:
        self.state = state

    def list(self) -> list[Feature]:
        return list(self.state.features.values())

    def get(self, function_id: UUID, *, lock: bool = False) -> Feature:
        return self.state.features[function_id]

    def insert(self, data: FeatureInput) -> Feature:
        row = Feature(uuid4(), **data.model_dump(), revision="new")
        self.state.features[row.function_id] = row
        return row

    def update(self, function_id: UUID, data: FeatureInput) -> Feature:
        row = replace(self.state.features[function_id], **data.model_dump())
        self.state.features[function_id] = row
        return row


class Work:
    """交易測試替身：失敗還原資料與稽核，不連外部 DB。"""

    def __init__(self) -> None:
        self.state = State({DEPT: Department(DEPT, "SYS", "系統管理", True, "r1")})
        self.departments = Departments(self.state)
        self.features = Features(self.state)
        self.fail_audit = False
        self.fail_commit = False
        self.revoke_during_write = False
        self.committed = False
        self.authorization_checks = 0

    def __enter__(self) -> Self:
        self.snapshot = deepcopy(self.state)
        self.committed = False
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self.committed:
            self.state.departments = self.snapshot.departments
            self.state.features = self.snapshot.features
            self.state.events = self.snapshot.events

    def authorize(self, user: Identity, decision: Decision) -> None:
        self.authorization_checks += 1
        if self.revoke_during_write:
            raise DeniedError

    def record(self, event: ChangeEvent) -> None:
        if self.fail_audit:
            raise UnavailableError
        self.state.events.append(event)
        rows = self.state.departments if event.kind == "department" else self.state.features
        rows[event.resource_id] = replace(rows[event.resource_id], revision="r2")

    def commit(self) -> None:
        if self.fail_commit:
            raise ConflictError
        self.committed = True


@pytest.fixture
def module() -> EModule:
    access, auth, grants, work = Access(), Auth(), Grants(), Work()
    return EModule(access, auth, grants, lambda: work)


@pytest.fixture
def e_client(module: EModule) -> Iterator[TestClient]:
    app.dependency_overrides[get_e_module] = lambda: module
    with TestClient(app, follow_redirects=False) as client:
        client.cookies.set(SESSION_COOKIE_NAME, TOKEN)
        yield client
    app.dependency_overrides.pop(get_e_module, None)


@pytest.mark.parametrize("effect", ["deny", "unset", "unresolved"])
def test_role_must_explicitly_allow_even_when_department_would_allow(
    module: EModule, effect: str
) -> None:
    module.access.effects[(DEPARTMENTS, Action.EDIT)] = effect
    with pytest.raises(DeniedError):
        module.save_department(
            identity(),
            DepartmentInput(department_code="SYS", department_name="修改"),
            department_id=DEPT,
            expected_revision="r1",
        )
    assert module.unit_of_work().state.events == []


def test_role_allow_is_not_capped_by_department_flag(module: EModule) -> None:
    # 合同只接受 G 已解析的職務 allow，不讀取部門 bool_or／can_edit 作為上限。
    module.save_department(
        identity(),
        DepartmentInput(department_code="SYS", department_name="新名稱"),
        department_id=DEPT,
        expected_revision="r1",
    )
    assert module.unit_of_work().state.departments[DEPT].department_name == "新名稱"


@pytest.mark.parametrize(
    "change",
    [
        {"system_management": False},
        {"admin_access": False},
        {"session_audience": "frontend"},
    ],
)
def test_management_requires_explicit_eligibility(
    module: EModule, change: dict[str, object]
) -> None:
    with pytest.raises(DeniedError):
        module.list_departments(replace(identity(), **change))


def test_scope_limits_list_and_prevents_cross_department_edit(module: EModule) -> None:
    module.access.scope = DataScope("departments", frozenset({DEPT}))
    work = module.unit_of_work()
    work.state.departments[OTHER_DEPT] = Department(OTHER_DEPT, "FIN", "財務", True, "r1")
    assert [row.department_id for row in module.list_departments(identity())] == [DEPT]
    with pytest.raises(DeniedError):
        module.save_department(
            identity(),
            DepartmentInput(department_code="FIN", department_name="入侵"),
            department_id=OTHER_DEPT,
            expected_revision="r1",
        )


def test_company_read_does_not_imply_write_or_management(module: EModule) -> None:
    definition = Capability("invoice.review", "審閱", "財務", "/invoices", frozenset(Action), False)
    user = replace(identity(), system_management=False)
    module.access.effects[(definition.code, Action.EDIT)] = "unset"
    require_permission(
        module.access, user, definition.code, Action.VIEW, Target(OTHER_DEPT), definition=definition
    )
    with pytest.raises(DeniedError):
        require_permission(
            module.access,
            user,
            definition.code,
            Action.EDIT,
            Target(OTHER_DEPT),
            definition=definition,
        )
    with pytest.raises(DeniedError):
        module.list_departments(user)


@pytest.mark.parametrize("kind", ["function_disabled", "wrong_user", "unknown_action"])
def test_guard_rejects_stale_or_mismatched_authority(module: EModule, kind: str) -> None:
    module.access.active = kind != "function_disabled"
    module.access.mismatch = kind == "wrong_user"
    with pytest.raises(DeniedError):
        module.check(
            identity(), DEPARTMENTS, Action.DELETE if kind == "unknown_action" else Action.VIEW
        )


def test_old_or_frontend_cookie_cannot_enter_e(module: EModule) -> None:
    with pytest.raises(UnauthenticatedError):
        verified_identity(module.access, SecretStr("signed-int-legacy-cookie"))
    module.access.user = replace(identity(), session_audience="frontend")
    with pytest.raises(DeniedError):
        verified_identity(module.access, SecretStr(TOKEN))


def test_expired_session_and_revocation_take_effect_next_request(module: EModule) -> None:
    module.identity(SecretStr(TOKEN))
    module.access.user = replace(identity(), expires_at=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(UnauthenticatedError):
        module.identity(SecretStr(TOKEN))
    module.access.user = None
    with pytest.raises(UnauthenticatedError):
        module.identity(SecretStr(TOKEN))


def test_duplicate_department_and_stale_revision_leave_no_partial_changes(module: EModule) -> None:
    with pytest.raises(ConflictError):
        module.save_department(
            identity(), DepartmentInput(department_code="SYS", department_name="重複")
        )
    with pytest.raises(ConflictError):
        module.save_department(
            identity(),
            DepartmentInput(department_code="SYS", department_name="過期"),
            department_id=DEPT,
            expected_revision="old",
        )
    assert module.unit_of_work().state.departments[DEPT].department_name == "系統管理"
    assert module.unit_of_work().state.events == []


@pytest.mark.parametrize("failure", ["fail_audit", "fail_commit", "revoke_during_write"])
def test_transaction_and_audit_failures_roll_back(module: EModule, failure: str) -> None:
    work = module.unit_of_work()
    setattr(work, failure, True)
    with pytest.raises(EError):
        module.save_department(
            identity(), DepartmentInput(department_code="NEW", department_name="新部門")
        )
    assert len(work.state.departments) == 1
    assert work.state.events == []
    assert work.authorization_checks == 1


@pytest.mark.parametrize(
    "impact", [DepartmentImpact(1), DepartmentImpact(0, 1), DepartmentImpact(0, 0, 1)]
)
def test_department_disable_blocks_each_live_reference(
    module: EModule, impact: DepartmentImpact
) -> None:
    module.unit_of_work().departments.impact_value = impact
    with pytest.raises(DepartmentInUseError):
        module.save_department(
            identity(),
            DepartmentInput(department_code="SYS", department_name="系統管理", is_active=False),
            department_id=DEPT,
            expected_revision="r1",
        )
    assert module.unit_of_work().state.departments[DEPT].is_active


def test_department_disable_preserves_history_and_records_actor(module: EModule) -> None:
    work = module.unit_of_work()
    module.save_department(
        identity(),
        DepartmentInput(department_code="SYS", department_name="系統管理", is_active=False),
        department_id=DEPT,
        expected_revision="r1",
    )
    assert not work.state.departments[DEPT].is_active
    assert work.state.events[0].actor_id == ACTOR
    assert work.state.events[0].before["is_active"] is True
    assert work.state.events[0].after["is_active"] is False


def test_catalog_cannot_create_executable_route_or_unknown_action(module: EModule) -> None:
    with pytest.raises(ValidationError):
        FeatureInput(function_code="evil", function_name="外連", route="https://evil.invalid")
    with pytest.raises(DeniedError):
        module.save_feature(
            identity(), FeatureInput(function_code="unknown", function_name="未知", is_active=True)
        )
    row = module.save_feature(
        identity(), FeatureInput(function_code="future", function_name="待整合")
    )
    assert row.is_active is False
    with pytest.raises(DeniedError):
        capability("future")


def test_matrix_cannot_grant_unknown_action_or_exceed_delegation(module: EModule) -> None:
    for code, action in [
        (DEPARTMENTS, Action.EDIT),
        ("unknown", Action.VIEW),
        (DEPARTMENTS, Action.DELETE),
    ]:
        with pytest.raises(DeniedError):
            module.save_matrix(
                identity(),
                DEPT,
                MatrixChange(
                    expected_revision="g-1",
                    rows=(GrantRow(function_code=code, actions=frozenset({action})),),
                ),
            )
    assert module.grants.calls == 0


def test_matrix_uses_g_version_and_writer(module: EModule) -> None:
    change = MatrixChange(
        expected_revision="g-1",
        rows=(GrantRow(function_code=DEPARTMENTS, actions=frozenset({Action.VIEW})),),
    )
    module.save_matrix(identity(), DEPT, change)
    assert module.grants.calls == 1
    with pytest.raises(ConflictError):
        module.save_matrix(identity(), DEPT, change)


def test_matrix_cannot_revoke_outside_delegation(module: EModule) -> None:
    module.grants.matrix = Matrix(
        "g-1", (GrantRow(function_code=DEPARTMENTS, actions=frozenset({Action.EDIT})),), ()
    )
    with pytest.raises(DeniedError):
        module.save_matrix(
            identity(),
            DEPT,
            MatrixChange(expected_revision="g-1", rows=(GrantRow(function_code=DEPARTMENTS),)),
        )


@pytest.mark.parametrize(
    "path", ["/system/departments", "/system/features", "/workspace", "/account"]
)
def test_unconfigured_adapters_fail_closed_without_touching_db(
    client: TestClient, path: str
) -> None:
    response = client.get(path, headers={"Cookie": f"{SESSION_COOKIE_NAME}={TOKEN}"})
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


def test_fake_identity_headers_do_not_replace_a_session(client: TestClient) -> None:
    response = client.get(
        "/system/departments", headers={"X-User-ID": str(ACTOR), "X-Role": "admin"}
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?")


def test_department_screen_escapes_data_and_has_no_dev_or_public_links(
    e_client: TestClient, module: EModule
) -> None:
    work = module.unit_of_work()
    work.state.departments[DEPT] = replace(
        work.state.departments[DEPT], department_name="<script>danger()</script>"
    )
    response = e_client.get("/system/departments")
    assert response.status_code == 200
    assert "&lt;script&gt;" in response.text
    assert "<script>danger()" not in response.text
    assert 'action="/dev/' not in response.text
    assert 'name="robots" content="noindex, nofollow"' in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "http://evil.invalid"},
        {"Origin": "https://testserver"},
        {"Origin": "null"},
        ORIGIN | {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_mutations_reject_missing_or_wrong_origin(
    e_client: TestClient, headers: dict[str, str]
) -> None:
    response = e_client.post(
        "/system/departments",
        data={"department_code": "NEW", "department_name": "新部門"},
        headers=headers,
    )
    assert response.status_code == 403


def test_direct_http_without_permission_is_denied(e_client: TestClient, module: EModule) -> None:
    module.access.effects[(DEPARTMENTS, Action.CREATE)] = "unset"
    response = e_client.post(
        "/system/departments",
        data={"department_code": "NEW", "department_name": "新部門"},
        headers=ORIGIN,
    )
    assert response.status_code == 403
    assert len(module.unit_of_work().state.departments) == 1


@pytest.mark.parametrize(
    "extra", [{"role": "admin"}, {"user_id": str(uuid4())}, {"department_id": str(OTHER_DEPT)}]
)
def test_profile_cannot_modify_roles_membership_or_other_user(
    e_client: TestClient, module: EModule, extra: dict[str, str]
) -> None:
    response = e_client.post("/account", data={"display_name": "安全名稱", **extra}, headers=ORIGIN)
    assert response.status_code == 400
    assert module.auth.patch is None


def test_login_uses_common_uuid_and_nonpersistent_httponly_cookie(
    e_client: TestClient, module: EModule
) -> None:
    response = e_client.post(
        "/login",
        data={
            "email": "synthetic@example.invalid",
            "password": "synthetic-password",
            "next": "https://evil.invalid",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Max-Age" not in cookie and "expires=" not in cookie.lower()
    assert response.headers["location"] == "/workspace"
    assert module.identity(SecretStr(TOKEN)).user_id == ACTOR


def test_login_rejects_frontend_session_without_sending_cookie(
    e_client: TestClient, module: EModule
) -> None:
    module.access.user = replace(identity(), session_audience="frontend")
    response = e_client.post(
        "/login",
        data={"email": "synthetic@example.invalid", "password": "synthetic-marker-password"},
        headers=ORIGIN,
    )
    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert "synthetic-marker-password" not in response.text
    assert module.auth.revoked


def test_logout_revokes_shared_session(e_client: TestClient, module: EModule) -> None:
    response = e_client.post("/logout", headers=ORIGIN)
    assert response.status_code == 303
    assert module.auth.revoked
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_password_ui_delegates_b_and_never_echoes_password(
    e_client: TestClient, module: EModule
) -> None:
    response = e_client.post(
        "/auth/password/forgot", data={"email": "synthetic@example.invalid"}, headers=ORIGIN
    )
    assert response.status_code == 200 and module.auth.reset_requests == 1
    response = e_client.post(
        "/auth/password/change",
        data={"current_password": "synthetic-old", "new_password": "synthetic-new"},
        headers=ORIGIN,
    )
    assert response.status_code == 303 and module.auth.changed
    assert "synthetic-old" not in response.text and "synthetic-new" not in response.text
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_no_fake_password_reset_success_when_b_missing(client: TestClient) -> None:
    response = client.post(
        "/auth/password/forgot", data={"email": "synthetic@example.invalid"}, headers=ORIGIN
    )
    assert response.status_code == 503
    assert "寄出" not in response.text


def test_menu_and_next_request_reflect_authority_change(
    e_client: TestClient, module: EModule
) -> None:
    assert 'href="/system/departments"' in e_client.get("/workspace").text
    module.access.effects[(DEPARTMENTS, Action.VIEW)] = "deny"
    assert 'href="/system/departments"' not in e_client.get("/workspace").text
    assert e_client.get("/system/departments").status_code == 403


@pytest.mark.parametrize(
    "page",
    [
        f"/system/departments/{DEPT}",
        "/system/features",
        f"/system/departments/{DEPT}/permissions",
        "/account",
        "/workspace",
    ],
)
def test_all_e_templates_render_with_test_ports(e_client: TestClient, page: str) -> None:
    response = e_client.get(page)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_create_and_update_departments_over_http(e_client: TestClient, module: EModule) -> None:
    response = e_client.post(
        "/system/departments",
        data={"department_code": "NEW", "department_name": "新部門", "is_active": "true"},
        headers=ORIGIN,
    )
    assert response.status_code == 303
    work = module.unit_of_work()
    created = next(row for row in work.state.departments.values() if row.department_code == "NEW")
    response = e_client.post(
        f"/system/departments/{created.department_id}",
        data={
            "department_code": "NEW",
            "department_name": "修改部門",
            "is_active": "true",
            "expected_revision": created.revision,
        },
        headers=ORIGIN,
    )
    assert response.status_code == 303
    assert work.state.departments[created.department_id].department_name == "修改部門"
    assert len(work.state.events) == 2


def test_create_update_feature_and_reject_code_change(
    e_client: TestClient, module: EModule
) -> None:
    response = e_client.post(
        "/system/features",
        data={
            "function_code": DEPARTMENTS,
            "function_name": "部門管理",
            "app_scope": "admin",
            "is_active": "true",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 303
    row = next(iter(module.unit_of_work().state.features.values()))
    assert e_client.get(f"/system/features/{row.function_id}").status_code == 200
    response = e_client.post(
        f"/system/features/{row.function_id}",
        data={
            "function_code": FEATURES,
            "function_name": "偷換功能",
            "app_scope": "admin",
            "expected_revision": row.revision,
        },
        headers=ORIGIN,
    )
    assert response.status_code == 400
    assert module.unit_of_work().state.features[row.function_id].function_code == DEPARTMENTS


def test_matrix_http_sends_only_current_registry_rows_to_g(
    e_client: TestClient, module: EModule
) -> None:
    response = e_client.post(
        f"/system/departments/{DEPT}/permissions",
        data={
            "expected_revision": "g-1",
            "grant": f"{DEPARTMENTS}:view",
        },
        headers=ORIGIN,
    )
    assert response.status_code == 303
    assert module.grants.calls == 1
    assert module.grants.matrix.rows[0].actions == frozenset({Action.VIEW})


def test_adapter_internal_error_does_not_leak_to_response_or_logs(
    e_client: TestClient,
    module: EModule,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail(email: str) -> None:
        raise RuntimeError("synthetic-private-connection-marker")

    monkeypatch.setattr(module.auth, "request_password_reset", fail)
    response = e_client.post(
        "/auth/password/forgot", data={"email": "synthetic@example.invalid"}, headers=ORIGIN
    )
    assert response.status_code == 503
    assert "synthetic-private-connection-marker" not in response.text
    assert "synthetic-private-connection-marker" not in caplog.text


def test_login_unavailable_does_not_touch_legacy_account_store(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "synthetic@example.invalid", "password": "synthetic-password"},
        headers=ORIGIN,
    )
    assert response.status_code == 503
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("path", ["/login", "/system/departments"])
def test_malformed_origin_is_rejected_without_internal_error(
    e_client: TestClient, path: str
) -> None:
    response = e_client.post(
        path,
        data={"email": "synthetic@example.invalid", "password": "synthetic-password"},
        headers={"Origin": "http://["},
    )
    assert response.status_code == 403


def test_company_catalog_menu_matches_actual_scope(e_client: TestClient, module: EModule) -> None:
    module.access.scope = DataScope("departments", frozenset({DEPT}))
    page = e_client.get("/workspace")
    assert page.status_code == 200
    assert 'href="/system/features"' not in page.text
    assert e_client.get("/system/features").status_code == 403
