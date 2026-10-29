"""模組名稱：app.schemas.e_ports；功能說明：E 與 B／G／DB 的接線協定，無正式替身。"""

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from pydantic import SecretStr

from app.schemas.e_access import AccessSnapshot
from app.schemas.e_admin import (
    Action,
    ChangeEvent,
    DataScope,
    Decision,
    Department,
    DepartmentImpact,
    DepartmentInput,
    Feature,
    FeatureInput,
    Identity,
    IssuedSession,
    Matrix,
    MatrixChange,
    Profile,
    ProfilePatch,
)


class AccessPort(Protocol):
    """網站 Session 及目前部門勾選接點；驗證當前帳號、profile、憑證版本與部門關係。"""

    def authenticate(self, token: SecretStr) -> Identity | None:
        """驗證管理站適用性；無效回 None，來源故障拋 UnavailableError。"""
        ...

    def decide(self, identity: Identity, code: str, action: Action) -> Decision:
        """只依目前有效部門功能／操作，不另指定管理者，也不合併其他部門。"""
        ...

    def effective_access(self, identity: Identity) -> AccessSnapshot:
        """回傳本人現行部門及完整已登錄管理功能的逐操作決策，缺資料不可宣稱 complete。"""
        ...


class AuthPort(Protocol):
    """ADMIN 登入接點；讀共用帳號與部門資料組合網站 Session，不走 /dev。"""

    def login(self, email: str, password: SecretStr) -> IssuedSession:
        """驗帳密與有效員工、讀部門勾選組 Session；最多更新登入紀錄，不另建 DB 資料。"""
        ...

    def logout(self, token: SecretStr) -> None:
        """結束網站登入；本版由路由清除簽章 Cookie，不寫入 DB Session。"""
        ...

    def profile(self, identity: Identity) -> Profile:
        """只取得已驗證操作者本人資料。"""
        ...

    def update_profile(self, identity: Identity, patch: ProfilePatch) -> None:
        """按帳號組核定白名單更新本人；需原子 audit，不能修改角色或部門。"""
        ...

    def request_password_reset(self, email: str) -> None:
        """B 同一 Token／寄信／限流流程；使用固定 ADMIN redirect，不接受使用者 URL。"""
        ...

    def change_password(self, identity: Identity, current: SecretStr, new: SecretStr) -> None:
        """B 驗證目前密碼與政策，更新 credential_version 並撤銷舊 Session。"""
        ...


class GrantPort(Protocol):
    """G 唯一矩陣來源；E 不直接操作 department_permissions。"""

    def read(self, identity: Identity, department_id: UUID) -> Matrix:
        """讀取現行版本與依操作者資格解析的可授上限。"""
        ...

    def replace(self, identity: Identity, department_id: UUID, change: MatrixChange) -> None:
        """G 同交易重驗資格／範圍／可授上限／最後管理者／版本並稽核；未提交列保留。"""
        ...


class DepartmentStore(Protocol):
    """E 部門 repository；參數必須包含已驗證 scope。"""

    def list(self, scope: DataScope) -> list[Department]:
        """在查詢層限制部門範圍。"""
        ...

    def get(self, department_id: UUID, scope: DataScope, *, lock: bool = False) -> Department:
        """範圍外視為不存在；寫入先鎖定目標。"""
        ...

    def insert(self, data: DepartmentInput) -> Department:
        """新增部門，不自行提交。"""
        ...

    def update(self, department_id: UUID, data: DepartmentInput) -> Department:
        """更新指定部門；不搬移帳號、不刪歷史。"""
        ...

    def impact(self, department_id: UUID) -> DepartmentImpact:
        """計入所有主要部門參照，包括 disabled 帳號，及目前成員與有效 Session。"""
        ...


class FeatureStore(Protocol):
    """E 功能目錄 repository；目錄是公司級資源。"""

    def list(self) -> list[Feature]:
        """列出功能目錄。"""
        ...

    def get(self, function_id: UUID, *, lock: bool = False) -> Feature:
        """取得功能；寫入先鎖定。"""
        ...

    def insert(self, data: FeatureInput) -> Feature:
        """新增目錄，不代表產生新的可執行程式。"""
        ...

    def update(self, function_id: UUID, data: FeatureInput) -> Feature:
        """保留穩定代碼，更新名稱及啟用狀態。"""
        ...


class UnitOfWork(Protocol):
    """Service 控制交易；正式 adapter 必須實作持久 audit 及原子版本檢查。"""

    departments: DepartmentStore
    features: FeatureStore

    def __enter__(self) -> Self:
        """建立單一交易。"""
        ...

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """未成功 commit 一律 rollback 並關閉連線。"""
        ...

    def record(self, event: ChangeEvent) -> None:
        """共同 journal 以 expected_revision 做 CAS 並持久化 audit／失效通知。"""
        ...

    def authorize(self, identity: Identity, decision: Decision) -> None:
        """同交易重驗／鎖定身分與政策版本，避免檢查後撤權仍成功寫入。"""
        ...

    def commit(self) -> None:
        """包含延遲外鍵驗證；失敗不得留下半筆更新。"""
        ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
