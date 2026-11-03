"""E2E-1：以 ADMIN 專案的 .venv 啟動真實 ADMIN 服務（uvicorn）。

只在**測試行程內**做三件事（正式功能目錄與權限設定不變）：
  1. 在功能目錄加入 inv.intake（正式目錄刻意沒有），並重建登入 adapter 以便授權依 DB 的 department_permissions 判斷；
  2. 以測試專用的 CredentialProvider 呼叫 API：用測試帳號密碼走 API 真實的 /auth/login 取得該使用者的 Session Token
     （**不是** R／P／B 任何候選方案，也不驗證 ADMIN → API 授權設計）；
  3. /cases 的案件範圍政策改為「同部門」（正式預設為拒絕）；Storage 換成檔案系統替身。
安全：資料庫與 API 位址必須是回送位址，否則拒絕啟動。
"""

import dataclasses
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

for name in ("DATABASE_URL", "E2E_API_BASE"):
    value = os.environ[name].replace("postgresql+psycopg", "postgresql")
    if urlsplit(value).hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("E2E 只允許連本機服務")

import fakes  # noqa: E402
import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import Request  # noqa: E402

from app.integrations.api_intake_client import ApiIntakeClient  # noqa: E402
from app.integrations.supabase_storage import S3StorageClient  # noqa: E402
from app.routers import intake as intake_router  # noqa: E402
from app.schemas.case import CaseScope  # noqa: E402
from app.schemas.e_admin import Action  # noqa: E402
from app.schemas.intake import OutboundRequest  # noqa: E402
from app.services import case_service  # noqa: E402
from app.services.admin_login_service import DepartmentLoginAdapter  # noqa: E402
from app.services.e_admin_service import EModule, get_e_module  # noqa: E402
from app.services.e_registry import Capability  # noqa: E402

S3StorageClient.from_settings = fakes.FileStorage.from_settings  # type: ignore[method-assign,assignment]

from app.main import app  # noqa: E402

API_BASE = os.environ["E2E_API_BASE"]
USERS = json.loads(
    os.environ["E2E_USERS_JSON"]
)  # {user_id: {"email": ..., "password": ...}}（合成測試帳號）


def _module() -> EModule:
    registry = (
        *get_e_module().registry,
        Capability(
            "inv.intake",
            "新增發票（E2E 測試用）",
            "發票作業",
            "/intake",
            frozenset({Action.VIEW, Action.CREATE}),
            system_only=False,
        ),
    )
    adapter = DepartmentLoginAdapter(registry)
    return EModule(access=adapter, auth=adapter, registry=registry)


MODULE = _module()


class TestOnlyBearer:
    """測試專用：以測試帳號密碼走 API 真實登入，之後帶該使用者的真實 Session Token。"""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.token: str | None = None

    def headers(self, request: OutboundRequest) -> dict[str, str]:
        if self.token is None:
            account = USERS[self.user_id]
            response = httpx.post(
                f"{API_BASE}/api/v1/auth/login",
                json={"email": account["email"], "password": account["password"]},
                timeout=10,
            )
            response.raise_for_status()
            self.token = response.json()["session_token"]
        return {"Authorization": f"Bearer {self.token}"}


class QuickClient(ApiIntakeClient):
    """檢核與提交的逾時縮短為 2 秒，讓『回應遲到』的案例不必等 10 秒。"""

    def _call(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if kwargs.get("timeout", 10.0) <= 10.0:
            kwargs["timeout"] = 2.0
        return super()._call(*args, **kwargs)


_providers: dict[str, TestOnlyBearer] = {}


def e2e_client(request: Request) -> ApiIntakeClient:
    user_id = str(request.state.intake_identity.user_id)
    provider = _providers.setdefault(user_id, TestOnlyBearer(user_id))
    return QuickClient(API_BASE, provider)


class SameDepartment:
    def scope(self, identity, departments):  # type: ignore[no-untyped-def]
        return CaseScope("department", identity.selected_department_id)


app.dependency_overrides[get_e_module] = lambda: MODULE
app.dependency_overrides[intake_router.get_intake_client] = e2e_client
app.dependency_overrides[case_service.get_case_policy] = SameDepartment

if __name__ == "__main__":
    assert dataclasses.is_dataclass(MODULE)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["E2E_ADMIN_PORT"]), log_level="warning")
