"""模組名稱：app.schemas.e_admin；功能說明：E 的 UUID、有效授權及管理表單契約。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class EError(Exception):
    """僅傳遞固定訊息，禁止附加資料庫、密碼或 Token 內容。"""

    status = 400
    message = "輸入資料不正確"

    def __init__(self) -> None:
        super().__init__(self.message)


class UnavailableError(EError):
    """正式共同接點尚未就緒。"""

    status = 503
    message = "此功能尚待共同服務接線，請稍後再試"


class UnauthenticatedError(EError):
    """共用認證確認沒有有效的管理站 Session。"""

    status = 401
    message = "請重新登入管理站"


class DeniedError(EError):
    """功能、職務、系統管理資格或資料範圍不符。"""

    status = 403
    message = "沒有權限執行此操作"


class WelcomeReadOnlyError(EError):
    """歡迎頁是不可撤銷的基礎唯讀首頁。"""

    message = "歡迎頁固定可查看，不提供新增、修改、刪除或停用"


class MissingError(EError):
    """目標不存在；不可透過錯誤洩漏範圍外資料。"""

    status = 404
    message = "找不到資料"


class ConflictError(EError):
    """代碼重複或已被其他管理者更新。"""

    status = 409
    message = "代碼已存在或資料已變更，請重新載入後再試"


class DepartmentInUseError(ConflictError):
    """部門仍有成員、主要部門參照或有效 Session。"""

    message = "部門仍被使用，請先由帳號及授權流程完成移轉或撤銷"


class Action(StrEnum):
    """與 v1.3 部門矩陣欄位對應的已知操作。"""

    VIEW = "view"
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"


@dataclass(frozen=True)
class Identity:
    """只能由 B／G 的權威 adapter 建立；每次請求重新驗證，不從 HTTP 宣告建構。"""

    user_id: UUID
    session_id: UUID
    selected_department_id: UUID
    credential_version: int
    expires_at: datetime
    admin_access: bool
    session_audience: Literal["admin", "frontend"]
    # 舊 DTO 相容欄位；本版不讀取作授權，沒有初始管理員或額外身分設定。
    system_management: bool = False


@dataclass(frozen=True)
class DataScope:
    """已核定資料範圍；部門集合不含其他部門隱含的權限。"""

    kind: Literal["none", "own", "departments", "company"] = "none"
    department_ids: frozenset[UUID] = frozenset()

    def covers(self, actor_id: UUID, target: "Target") -> bool:
        """驗證具體資源；目錄／全域操作必須明確取得 company 範圍。"""
        if self.kind == "company":
            return True
        if target.global_resource:
            return False
        if self.kind == "departments":
            return target.department_id is not None and target.department_id in self.department_ids
        if self.kind == "own":
            return target.owner_id == actor_id and (
                not self.department_ids or target.department_id in self.department_ids
            )
        return False


@dataclass(frozen=True)
class Target:
    """由伺服器載入的資源擁有者／部門；不得把表單 owner_id 當成事實。"""

    department_id: UUID | None = None
    owner_id: UUID | None = None
    global_resource: bool = False


@dataclass(frozen=True)
class Decision:
    """G 解析目前部門基本許可、管理職務及獨立資料範圍後的綁定決策。"""

    user_id: UUID
    session_id: UUID
    selected_department_id: UUID
    function_code: str
    action: Action
    role_effect: Literal["allow", "deny", "unset", "unresolved"]
    function_active: bool
    policy_version: str
    valid_until: datetime
    scope: DataScope = field(default_factory=DataScope)
    department_effect: Literal["allow", "deny", "unset", "unresolved"] = "unresolved"
    # 僅目前部門；舊 role_effect 欄位不影響權限。
    authorization_basis: Literal["department"] = "department"


class InputModel(BaseModel):
    """拒絕表單／JSON 夾帶身分、角色或其他未允許欄位。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DepartmentInput(InputModel):
    """部門代碼區分大小寫，沿用 v1.3 UNIQUE 語意；不提供刪除。"""

    department_code: str = Field(min_length=1, max_length=100)
    department_name: str = Field(min_length=1, max_length=200)
    is_active: bool = True

    @field_validator("department_code", "department_name", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        """去除前後空白，拒絕控制字元；上限為管理介面的輸入限制。"""
        if isinstance(value, str):
            value = value.strip()
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError("不接受控制字元")
        return value


class FeatureInput(InputModel):
    """僅維護 v1.3 已有欄位；route／action 來自固定 registry。"""

    function_code: str = Field(min_length=1, max_length=100)
    function_name: str = Field(min_length=1, max_length=200)
    app_scope: Literal["admin", "frontend", "both"] = "admin"
    is_active: bool = False

    _trim = field_validator("function_code", "function_name", mode="before")(
        DepartmentInput.trim_text.__func__
    )


@dataclass(frozen=True)
class Department:
    """部門安全投影；revision 由共同 journal 提供，不把 updated_at 當集合版本。"""

    department_id: UUID
    department_code: str
    department_name: str
    is_active: bool
    revision: str


@dataclass(frozen=True)
class Feature:
    """功能安全投影；不包含任意可執行 URL。"""

    function_id: UUID
    function_code: str
    function_name: str
    app_scope: str
    is_active: bool
    revision: str


@dataclass(frozen=True)
class DepartmentImpact:
    """停用前在同交易取得影響；不自動搬動帳號或刪除歷史。"""

    current_members: int = 0
    primary_references: int = 0
    live_sessions: int = 0

    @property
    def blocked(self) -> bool:
        """存在使用中的參照時暫拒絕停用，交由共同移轉流程處理。"""
        return bool(self.current_members or self.primary_references or self.live_sessions)


class GrantRow(InputModel):
    """E 對 G 提交的部門基礎矩陣；不代表職務授權或資料範圍。"""

    function_code: str
    actions: frozenset[Action] = frozenset()


class MatrixChange(InputModel):
    """提交各列以 G 發行的集合版本原子更新；未提交的其他組功能必須保留。"""

    expected_revision: str = Field(min_length=1, max_length=200)
    rows: tuple[GrantRow, ...]


@dataclass(frozen=True)
class Matrix:
    """G 的當前矩陣投影及可授出上限；未列出者不可授。"""

    revision: str
    rows: tuple[GrantRow, ...]
    delegable: tuple[GrantRow, ...]


class ProfilePatch(InputModel):
    """使用者已核定僅允許顯示名稱；帳號組 adapter 必須遵守相同白名單。"""

    display_name: str = Field(min_length=1, max_length=200)
    _trim = field_validator("display_name", mode="before")(DepartmentInput.trim_text.__func__)


@dataclass(frozen=True)
class Profile:
    """本人資訊白名單；不含密碼雜湊、職務寫入欄位或原始 Session。"""

    user_id: UUID
    display_name: str
    email: str


@dataclass(frozen=True)
class IssuedSession:
    """B 發行的 opaque 憑證；SecretStr 防止 repr 意外輸出，瀏覽器不持久保存。"""

    user_id: UUID
    token: SecretStr


@dataclass(frozen=True)
class ChangeEvent:
    """需同交易持久化的稽核事件；G／DB journal 同時負責版本及失效通知。"""

    actor_id: UUID
    kind: Literal["department", "feature"]
    resource_id: UUID
    expected_revision: str | None
    before: dict[str, str | bool] | None
    after: dict[str, str | bool]
