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

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.constants import SESSION_COOKIE_NAME
from app.core.exceptions import ForbiddenError, LoginRequiredError
from app.core.security import read_session_token
from app.db.models.user import User, UserRole
from app.db.repositories import user_repository
from app.db.session import get_db

# 資料庫 Session：每個請求一個，請求結束自動關閉
DbSession = Annotated[Session, Depends(get_db)]


def get_session_user_id(request: Request) -> int:
    """從登入 cookie 取出管理者 ID；未登入、遭竄改或過期時拋出 LoginRequiredError。"""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = read_session_token(token) if token else None
    if user_id is None:
        raise LoginRequiredError
    return user_id


def require_admin(
    request: Request,
    # NOTE: 必須排在 db 之前——未登入時先拋出例外，不必建立資料庫連線
    user_id: Annotated[int, Depends(get_session_user_id)],
    db: DbSession,
) -> User:
    """確認目前使用者是啟用中的管理者，並放入 request.state.admin 供樣板使用。

    Raises:
        LoginRequiredError: 帳號不存在或已停用。
        ForbiddenError: 帳號不是管理者。
    """
    user = user_repository.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise LoginRequiredError
    if user.role != UserRole.ADMIN:
        raise ForbiddenError("此帳號沒有後台權限")
    request.state.admin = user
    return user


# 需要管理者資料的路由：在參數宣告 `admin: CurrentAdmin`
CurrentAdmin = Annotated[User, Depends(require_admin)]
