"""模組名稱：app.services.e_registry；功能說明：E 固定功能路徑／操作 allowlist，非 DB seed。"""

import re
from dataclasses import dataclass

from app.constants import WELCOME_CODE, WELCOME_PATH
from app.schemas.e_admin import Action, DeniedError, UnavailableError


@dataclass(frozen=True)
class Capability:
    """可執行能力必須經程式審查加入，不能由目錄表單產生。"""

    code: str
    name: str
    category: str
    route: str
    actions: frozenset[Action]
    management: bool = True
    system_only: bool = True


# 不納入 G 的可選功能矩陣或 DB seed；新部門不必補寫 grant。
WELCOME_CAPABILITY = Capability(
    WELCOME_CODE,
    "歡迎頁",
    "系統內建",
    WELCOME_PATH,
    frozenset({Action.VIEW}),
    management=False,
    system_only=False,
)

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
        "/system/departments/{department_id}/permissions",
        frozenset({Action.VIEW, Action.EDIT}),
        system_only=False,
    ),
)


def validate_registry(definitions: tuple[Capability, ...]) -> None:
    """核對伺服器固定定義；未知 action、重複 code、外部及 /dev 路徑均拒絕。"""
    seen = set()
    for item in definitions:
        if (
            not re.fullmatch(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*", item.code)
            or item.code in seen
            or item.code == WELCOME_CODE
            or not item.name.strip()
            or not item.category.strip()
            or not item.actions
            or any(not isinstance(action, Action) for action in item.actions)
            or not re.fullmatch(
                r"/[A-Za-z0-9][A-Za-z0-9/_-]*(?:\{department_id\}[A-Za-z0-9/_-]*)?", item.route
            )
            or "//" in item.route
            or item.route == "/dev"
            or item.route.startswith("/dev/")
            or item.route == "/api/dev"
            or item.route.startswith("/api/dev/")
        ):
            raise UnavailableError
        seen.add(item.code)


def capability(code: str, definitions: tuple[Capability, ...] = REGISTRY) -> Capability:
    """取得已實作功能，未知代碼拒絕；不從請求載入 route／action。"""
    if code == WELCOME_CODE:
        return WELCOME_CAPABILITY
    for item in definitions:
        if item.code == code:
            return item
    raise DeniedError


def supported_actions(
    code: str, definitions: tuple[Capability, ...] = REGISTRY
) -> frozenset[Action]:
    """未知或尚未接入的功能沒有可授出的操作。"""
    try:
        return capability(code, definitions).actions
    except DeniedError:
        return frozenset()


def initial_manifest(definitions: tuple[Capability, ...] = REGISTRY) -> dict[str, object]:
    """產生可審查的 E07 初始資料；全部預設停用，不寫 DB 或指派任何 grant。"""
    validate_registry(definitions)
    return {
        "contract_version": "e-initial-functions/1",
        "schema_baseline": "v1.3 PortableAuth",
        "status": "review_required",
        "functions": [
            {
                "function_code": item.code,
                "function_name": item.name,
                "category": item.category,
                "route": item.route,
                "actions": sorted(action.value for action in item.actions),
                "app_scope": "admin",
                "is_active": False,
            }
            for item in definitions
        ],
        "pending_domains": ["finance", "procurement", "accounts", "knowledge"],
    }
