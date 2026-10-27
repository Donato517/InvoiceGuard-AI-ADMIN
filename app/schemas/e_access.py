"""模組名稱：app.schemas.e_access；功能說明：E02 本人有效部門與逐操作 scope 投影。"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.schemas.e_admin import Action, DataScope, Decision, DeniedError, Profile


class NoAdminFunctionsError(DeniedError):
    """有效身分未獲任何可執行管理功能，拒絕進入工作台。"""

    message = "目前沒有可使用的管理功能，請聯絡授權管理者"


@dataclass(frozen=True)
class EffectiveDepartment:
    """G／帳號來源提供的本人現行部門；不包含其他成員個資。"""

    department_id: UUID
    department_code: str
    department_name: str
    is_active: bool = True


@dataclass(frozen=True)
class AccessSnapshot:
    """權威來源對完整已登錄管理功能的決策；每個 code/action 都需有明確結果。"""

    user_id: UUID
    session_id: UUID
    selected_department_id: UUID
    policy_version: str
    valid_until: datetime
    departments: tuple[EffectiveDepartment, ...]
    decisions: tuple[Decision, ...]
    complete: bool = False


@dataclass(frozen=True)
class ActionAccess:
    """操作及其獨立 scope，不能把不同操作的範圍合併後擴權。"""

    action: Action
    action_label: str
    scope: DataScope
    scope_label: str


@dataclass(frozen=True)
class FunctionAccess:
    """名稱與 route 由伺服器固定 registry 提供，授權資料不能改寫路由。"""

    code: str
    name: str
    category: str
    route: str
    grants: tuple[ActionAccess, ...]

    @property
    def can_open(self) -> bool:
        """只有明確 view 許可才提供頁面連結，不由 create／edit 隱含 view。"""
        return any(grant.action == Action.VIEW for grant in self.grants)


@dataclass(frozen=True)
class SelfAccess:
    """E 驗證後可呈現的本人資訊，不含 Session Token／內部角色帳冊。"""

    user_id: UUID
    selected_department_id: UUID
    policy_version: str
    valid_until: datetime
    departments: tuple[EffectiveDepartment, ...]
    functions: tuple[FunctionAccess, ...]


@dataclass(frozen=True)
class SelfProfile:
    """本人基本資料與同一次請求驗證的授權投影。"""

    profile: Profile
    access: SelfAccess
