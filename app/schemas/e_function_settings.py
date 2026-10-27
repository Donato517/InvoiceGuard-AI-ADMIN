"""E 本機功能設定投影；只使用 v1.3 現有資料，不虛構主管或資料範圍欄位。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.e_admin import Action, DepartmentInput, InputModel


class FunctionListQuery(InputModel):
    """固定搜尋欄位，不接受 SQL 排序或外部返回網址。"""

    q: str = Field(default="", max_length=200)
    platform: Literal["all", "admin", "frontend", "both"] = "all"
    status: Literal["all", "active", "inactive"] = "all"
    page: int = Field(default=1, ge=1, le=1_000_000)

    _trim = field_validator("q", mode="before")(DepartmentInput.trim_text.__func__)


class FunctionStatusInput(InputModel):
    """狀態操作只能指定啟用旗標。"""

    is_active: bool


@dataclass(frozen=True)
class FunctionSetting:
    """功能字典安全欄位，沒有可由表單執行的 route。"""

    function_id: UUID
    function_code: str
    function_name: str
    app_scope: str
    is_active: bool


@dataclass(frozen=True)
class FunctionListPage:
    """當頁安全資料與已校正的列表條件。"""

    query: FunctionListQuery
    items: tuple[FunctionSetting, ...]
    total: int
    pages: int


@dataclass(frozen=True)
class FunctionPermission:
    """一部門的一功能及分離操作；updated_at 僅用於內容衝突辨識。"""

    function: FunctionSetting
    actions: frozenset[Action]
    updated_at: datetime | None = None


@dataclass(frozen=True)
class DepartmentFunctionSettings:
    """設定對象部門，不代表目前操作者的 Session 所選部門。"""

    department_id: UUID
    department_code: str
    department_name: str
    is_active: bool
    rows: tuple[FunctionPermission, ...]


@dataclass(frozen=True)
class MatrixChange:
    """單一功能的變更摘要，不代表操作者的管理資格。"""

    function: FunctionSetting
    before: frozenset[Action]
    after: frozenset[Action]

    @property
    def added(self) -> frozenset[Action]:
        """本次新增的操作。"""
        return self.after - self.before

    @property
    def removed(self) -> frozenset[Action]:
        """本次撤銷的操作。"""
        return self.before - self.after
