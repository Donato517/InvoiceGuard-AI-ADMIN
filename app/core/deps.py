"""
模組名稱：app.core.deps
功能說明：集中定義 FastAPI 相依注入（Depends）——資料庫 Session、管理者登入檢查。
使用方式：
    # 整個 router 都需要管理者登入
    router = APIRouter(dependencies=[Depends(require_admin)])

    # 需要使用管理者資料（例如寫入稽核紀錄的操作者）
    @router.post("/users/{user_id}/status")
    def update_status(db: DbSession, admin: CurrentAdmin) -> ...:
        ...
"""

import inspect
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.constants import SESSION_COOKIE_NAME
from app.core.config import settings
from app.core.exceptions import ForbiddenError, LoginRequiredError, NotFoundError
from app.core.security import read_session_token
from app.db.models.user import User, UserRole
from app.db.repositories import user_repository
from app.db.session import get_db


def get_session_db(request: Request) -> Iterator[Session | None]:
    """取得資料庫 Session；若本機離線未設定 DB，安全退回 None。"""
    override = request.app.dependency_overrides.get(get_db)
    if override is not None:
        if inspect.isgeneratorfunction(override):
            yield from override()
        else:
            yield override()
        return

    try:
        yield from get_db()
    except Exception:
        if settings.APP_ENV == "local" and not settings.is_vercel:
            yield None
            return
        raise


# 資料庫 Session：每個請求一個，請求結束自動關閉
DbSession = Annotated[Session | None, Depends(get_session_db)]

# 本機開發測試管理者
DEV_ADMIN_ID = 1
DEV_ADMIN = SimpleNamespace(
    id=DEV_ADMIN_ID,
    email="admin@example.com",
    display_name="本機測試管理者",
    role=UserRole.ADMIN,
    is_active=True,
)


def get_session_user_id(request: Request) -> int:
    """從登入 cookie 取出管理者 ID；未登入、遭竄改或過期時拋出 LoginRequiredError。"""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = _read_user_id(token) if token else None
    if user_id is None:
        raise LoginRequiredError
    return user_id


def _read_user_id(token: str) -> int | None:
    """驗證登入 cookie；本機沒有簽署金鑰時，殘留的 cookie 視為未登入。"""
    try:
        return read_session_token(token)
    except RuntimeError:
        # 雲端設定不合格（ModuleConfigurationError 亦屬 RuntimeError）維持拒絕並記錄，不改導向登入頁
        if settings.is_vercel:
            raise
        return None


def require_admin(
    request: Request,
    # NOTE: 必須排在 db 之前——未登入時先拋出例外，不必建立資料庫連線
    user_id: Annotated[int, Depends(get_session_user_id)],
    db: DbSession,
) -> User | SimpleNamespace:
    """確認目前使用者是啟用中的管理者，並放入 request.state.admin 供樣板使用。

    Raises:
        LoginRequiredError: 帳號不存在或已停用。
        ForbiddenError: 帳號不是管理者。
    """
    user: User | SimpleNamespace | None = None
    try:
        user = user_repository.get_by_id(db, user_id)
    except Exception:
        user = None

    if user is None:
        if settings.APP_ENV == "local" and not settings.is_vercel and user_id == DEV_ADMIN_ID:
            user = DEV_ADMIN
        else:
            raise LoginRequiredError
    if user.role != UserRole.ADMIN or not user.is_active:
        raise ForbiddenError("此帳號沒有後台權限")
    request.state.admin = user
    return user


# 需要管理者資料的路由：在參數宣告 `admin: CurrentAdmin`
CurrentAdmin = Annotated[User | SimpleNamespace, Depends(require_admin)]


def require_dev_pages() -> None:
    """保護本機 /dev 示範；帳號與檔案分別使用既有 DB 與 Vercel 模組。

    Raises:
        NotFoundError: APP_ENV 非 local、VERCEL=1，或解析後 module_backend 非 remote。
    """
    if not settings.dev_pages_enabled:
        raise NotFoundError


def require_cloud_db() -> None:
    """帳號 JSON router 的模式檢查；只檢查解析後 module_backend，不檢查 DB 設定或身分。

    Raises:
        NotFoundError: 解析後 module_backend 不是 cloud。
    """
    if settings.module_backend != "cloud":
        raise NotFoundError
