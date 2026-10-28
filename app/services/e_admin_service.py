"""模組名稱：app.services.e_admin_service；功能說明：E 部門、目錄、矩陣及本人流程。"""

from contextlib import suppress
from dataclasses import asdict, dataclass
from uuid import UUID

from pydantic import SecretStr

from app.schemas.e_access import NoAdminFunctionsError, SelfAccess, SelfProfile, SidebarEntry
from app.schemas.e_admin import (
    Action,
    ChangeEvent,
    ConflictError,
    Decision,
    DeniedError,
    Department,
    DepartmentInput,
    DepartmentInUseError,
    EError,
    Feature,
    FeatureInput,
    Identity,
    IssuedSession,
    Matrix,
    MatrixChange,
    ProfilePatch,
    Target,
    UnauthenticatedError,
    UnavailableError,
)
from app.schemas.e_ports import AccessPort, AuthPort, GrantPort, UnitOfWorkFactory
from app.services.e_access_service import read_self_access
from app.services.e_adapter import call_adapter
from app.services.e_authorization import require_permission, verified_identity
from app.services.e_registry import REGISTRY, Capability, capability, supported_actions

DEPARTMENTS = "sys.departments"
FEATURES = "sys.features"
GRANTS = "sys.department-permissions"


@dataclass
class EModule:
    """只由伺服器組裝真實 adapters；None 代表未接線，沒有本機放行模式。"""

    access: AccessPort | None = None
    auth: AuthPort | None = None
    grants: GrantPort | None = None
    unit_of_work: UnitOfWorkFactory | None = None
    registry: tuple[Capability, ...] = REGISTRY

    def identity(self, token: SecretStr | None) -> Identity:
        """每次請求向權威來源驗證身分，不接受角色／UUID Header。"""
        if token is None:
            raise UnauthenticatedError
        if self.access is None:
            raise UnavailableError
        return verified_identity(self.access, token)

    def check(
        self,
        identity: Identity,
        code: str,
        action: Action,
        target: Target | None = None,
    ) -> Decision:
        """方便其他服務使用相同 guard，回傳綁定當次請求的有效決策。"""
        if self.access is None:
            raise UnavailableError
        return require_permission(
            self.access, identity, code, action, target, definition=capability(code, self.registry)
        )

    def workspace_access(self, identity: Identity) -> SelfAccess:
        """取得完整登錄清單的有效功能；無管理功能回 403，缺來源回 503。"""
        if self.access is None:
            raise UnavailableError
        return read_self_access(self.access, identity, self.registry)

    def sidebar_menu(self, identity: Identity) -> tuple[SidebarEntry, ...]:
        """網站登入來源使用即時部門目錄；共同 adapter 仍依完整驗證投影呈現。"""
        from app.services.admin_login_service import DepartmentLoginAdapter

        if isinstance(self.access, DepartmentLoginAdapter):
            return self.access.sidebar_menu(identity)
        return tuple(
            SidebarEntry(item.code, item.name, item.route)
            for item in self.workspace_access(identity).functions
            if item.can_open
        )

    def list_departments(self, identity: Identity) -> list[Department]:
        """只查詢核定部門範圍。"""
        decision = self._decision(identity, DEPARTMENTS, Action.VIEW)
        with self._factory()() as work:
            return work.departments.list(decision.scope)

    def get_department(self, identity: Identity, department_id: UUID) -> Department:
        """在權限及 repository 兩層限制目標部門。"""
        decision = self._decision(identity, DEPARTMENTS, Action.VIEW, Target(department_id))
        with self._factory()() as work:
            return work.departments.get(department_id, decision.scope)

    def save_department(
        self,
        identity: Identity,
        data: DepartmentInput,
        *,
        department_id: UUID | None = None,
        expected_revision: str | None = None,
    ) -> Department:
        """新增或更新；驗權、影響檢查、異動、CAS 與 audit 同交易提交。"""
        target = Target(department_id, global_resource=department_id is None)
        action = Action.CREATE if department_id is None else Action.EDIT
        decision = self._decision(identity, DEPARTMENTS, action, target)
        with self._factory()() as work:
            work.authorize(identity, decision)
            before = None
            if department_id is not None:
                before = work.departments.get(department_id, decision.scope, lock=True)
                if not expected_revision or before.revision != expected_revision:
                    raise ConflictError
                if (
                    before.is_active
                    and not data.is_active
                    and work.departments.impact(department_id).blocked
                ):
                    raise DepartmentInUseError
                saved = work.departments.update(department_id, data)
            else:
                saved = work.departments.insert(data)
            work.record(
                ChangeEvent(
                    identity.user_id,
                    "department",
                    saved.department_id,
                    expected_revision,
                    self._department_values(before) if before else None,
                    data.model_dump(),
                )
            )
            saved = work.departments.get(saved.department_id, decision.scope)
            work.commit()
            return saved

    def list_features(self, identity: Identity) -> list[Feature]:
        """功能字典屬公司級目錄，不能把部門範圍提升為全域。"""
        self._decision(identity, FEATURES, Action.VIEW, Target(global_resource=True))
        with self._factory()() as work:
            return work.features.list()

    def get_feature(self, identity: Identity, function_id: UUID) -> Feature:
        """取得可編輯的功能安全投影。"""
        self._decision(identity, FEATURES, Action.VIEW, Target(global_resource=True))
        with self._factory()() as work:
            return work.features.get(function_id)

    def save_feature(
        self,
        identity: Identity,
        data: FeatureInput,
        *,
        function_id: UUID | None = None,
        expected_revision: str | None = None,
    ) -> Feature:
        """只啟用已實作的 admin 能力；代碼建立後不可改以免既有授權錯接。"""
        action = Action.CREATE if function_id is None else Action.EDIT
        decision = self._decision(identity, FEATURES, action, Target(global_resource=True))
        if data.is_active:
            capability(data.function_code, self.registry)
            if data.app_scope not in {"admin", "both"}:
                raise EError
        with self._factory()() as work:
            work.authorize(identity, decision)
            before = None
            if function_id is not None:
                before = work.features.get(function_id, lock=True)
                if not expected_revision or before.revision != expected_revision:
                    raise ConflictError
                if before.function_code != data.function_code:
                    raise EError
                saved = work.features.update(function_id, data)
            else:
                saved = work.features.insert(data)
            old = (
                None
                if before is None
                else {
                    key: value
                    for key, value in asdict(before).items()
                    if key not in {"function_id", "revision"}
                }
            )
            work.record(
                ChangeEvent(
                    identity.user_id,
                    "feature",
                    saved.function_id,
                    expected_revision,
                    old,
                    data.model_dump(),
                )
            )
            saved = work.features.get(saved.function_id)
            work.commit()
            return saved

    def read_matrix(self, identity: Identity, department_id: UUID) -> Matrix:
        """E 畫面只讀 G 的資料，不建第二套授權表。"""
        self._decision(identity, GRANTS, Action.VIEW, Target(department_id))
        if self.grants is None:
            raise UnavailableError
        return call_adapter(self.grants.read, identity, department_id)

    def save_matrix(self, identity: Identity, department_id: UUID, change: MatrixChange) -> None:
        """拒絕未知功能／操作／超額授權；G 必須在提交時再次驗證並原子寫入。"""
        self._decision(identity, GRANTS, Action.EDIT, Target(department_id))
        if self.grants is None:
            raise UnavailableError
        current = call_adapter(self.grants.read, identity, department_id)
        if current.revision != change.expected_revision:
            raise ConflictError
        ceiling = {row.function_code: row.actions for row in current.delegable}
        previous = {row.function_code: row.actions for row in current.rows}
        seen = set()
        for row in change.rows:
            if row.function_code in seen:
                raise EError
            seen.add(row.function_code)
            # 未註冊能力不能新增操作；既有非本組授權不得被無聲移除。
            existing = previous.get(row.function_code, frozenset())
            if not (row.actions - existing) <= supported_actions(row.function_code, self.registry):
                raise DeniedError
            if not (row.actions ^ existing) <= ceiling.get(row.function_code, frozenset()):
                raise DeniedError
        call_adapter(self.grants.replace, identity, department_id, change)

    def login(self, email: str, password: SecretStr) -> IssuedSession:
        """委派 B 建立 Session 並驗 ADMIN 資格；無業務功能可僅進歡迎頁。"""
        auth = self._auth()
        if self.access is None:
            raise UnavailableError
        issued = call_adapter(auth.login, email, password)
        try:
            identity = verified_identity(self.access, issued.token)
            if identity.user_id != issued.user_id:
                raise DeniedError
            # 只允許「完整且有效的權限來源確認沒有功能」這一種結果。
            # 其他缺接線、失效或不一致仍撤銷 Session 並拒絕。
            with suppress(NoAdminFunctionsError):
                self.workspace_access(identity)
        except EError:
            call_adapter(auth.logout, issued.token)
            raise
        return issued

    def logout(self, token: SecretStr | None) -> None:
        """先由 B 撤銷服務端 Session，不把清 Cookie 當成完整登出。"""
        if token is not None:
            call_adapter(self._auth().logout, token)

    def profile(self, identity: Identity) -> SelfProfile:
        """本人查詢不接受 URL user_id，並檢查 adapter 回傳識別。"""
        access = self.workspace_access(identity)
        profile = call_adapter(self._auth().profile, identity)
        if profile.user_id != identity.user_id:
            raise DeniedError
        return SelfProfile(profile, access)

    def update_profile(self, identity: Identity, patch: ProfilePatch) -> None:
        """白名單更新本人；實際寫入及核定欄位由共用認證 adapter 保護。"""
        call_adapter(self._auth().update_profile, identity, patch)

    def request_password_reset(self, email: str) -> None:
        """只呼叫 B 現有重設流程，不建立另一種 Token 或假寄信。"""
        call_adapter(self._auth().request_password_reset, email)

    def change_password(self, identity: Identity, current: SecretStr, new: SecretStr) -> None:
        """由 B 驗舊密碼、套用政策及撤銷憑證，E 不自行雜湊或產生 Token。"""
        call_adapter(self._auth().change_password, identity, current, new)

    def _decision(
        self,
        identity: Identity,
        code: str,
        action: Action,
        target: Target | None = None,
    ) -> Decision:
        if self.access is None:
            raise UnavailableError
        return self.check(identity, code, action, target)

    def _factory(self) -> UnitOfWorkFactory:
        if self.unit_of_work is None:
            raise UnavailableError
        return self.unit_of_work

    def _auth(self) -> AuthPort:
        if self.auth is None:
            raise UnavailableError
        return self.auth

    @staticmethod
    def _department_values(data: Department) -> dict[str, str | bool]:
        return {
            "department_code": data.department_code,
            "department_name": data.department_name,
            "is_active": data.is_active,
        }


def get_e_module() -> EModule:
    """組裝目前帳號與部門勾選的網站 Session 登入；不建立額外管理員資格。"""
    from dataclasses import replace

    from app.services.admin_login_service import DepartmentLoginAdapter

    routes = {DEPARTMENTS: "/department", FEATURES: "/functions", GRANTS: "/department"}
    registry = tuple(replace(item, route=routes[item.code]) for item in REGISTRY) + (
        Capability("sys.accounts", "帳號管理", "系統管理", "/accounts", frozenset(Action)),
    )
    adapter = DepartmentLoginAdapter(registry)
    return EModule(access=adapter, auth=adapter, registry=registry)
