"""模組名稱：app.services.e_registry；功能說明：E 固定功能路徑／操作 allowlist，非 DB seed。"""

from dataclasses import dataclass

from app.schemas.e_admin import Action, DeniedError


@dataclass(frozen=True)
class Capability:
    """可執行能力必須經程式審查加入，不能由目錄表單產生。"""

    code: str
    name: str
    category: str
    route: str
    actions: frozenset[Action]
    management: bool = True


REGISTRY = (
    Capability(
        "sys.departments",
        "部門管理",
        "系統管理",
        "/system/departments",
        frozenset({Action.VIEW, Action.CREATE, Action.EDIT}),
    ),
    Capability(
        "sys.features",
        "功能目錄",
        "系統管理",
        "/system/features",
        frozenset({Action.VIEW, Action.CREATE, Action.EDIT}),
    ),
    Capability(
        "sys.department-permissions",
        "部門功能設定",
        "系統管理",
        "/system/departments",
        frozenset({Action.VIEW, Action.EDIT}),
    ),
)


def capability(code: str) -> Capability:
    """取得已實作功能，未知代碼拒絕；不從請求載入 route／action。"""
    for item in REGISTRY:
        if item.code == code:
            return item
    raise DeniedError


def supported_actions(code: str) -> frozenset[Action]:
    """未知或尚未接入的功能沒有可授出的操作。"""
    try:
        return capability(code).actions
    except DeniedError:
        return frozenset()
