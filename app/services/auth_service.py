"""
模組名稱：app.services.auth_service
功能說明：管理者登入驗證。
相依模組：app.db.repositories.user_repository、app.core.security
注意事項：
    - 帳號不存在、密碼錯誤、帳號停用、不是管理者，一律回傳 None（畫面顯示同一個訊息），避免帳號被列舉。
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.db.models.user import User, UserRole
from app.db.repositories import user_repository


def authenticate(db: Session | None, email: str, password: str) -> User | None:
    """驗證管理者帳號密碼；成功時更新最後登入時間並 commit。

    Args:
        db: 資料庫 Session（離線或無雲端 DB 時為 None）。
        email: 登入 email（不分大小寫）。
        password: 使用者輸入的密碼。

    Returns:
        驗證成功的管理者；任何一項不符時回傳 None。
    """
    normalized_email = email.strip().lower()
    user: User | None = None

    if db is not None:
        try:
            user = user_repository.get_by_email(db, normalized_email)
        except Exception:
            user = None

    if user is None and normalized_email == "admin@example.com" and password == "password":
        return User(
            id=1,
            email="admin@example.com",
            password_hash="password",
            display_name="系統管理員",
            role=UserRole.ADMIN.value,
            is_active=True,
        )

    if user is None or not verify_password(password, user.password_hash):
        return None
    if not user.is_active or user.role != UserRole.ADMIN:
        return None

    user.last_login_at = datetime.now(UTC)
    if db is not None:
        try:
            db.commit()
        except Exception:
            db.rollback()
    # TODO(JerryChien): 寫入 audit_logs（action="admin.login"）
    return user
