"""
模組名稱：app.db.repositories.user_repository
功能說明：users 資料表的資料存取（登入查詢、使用者列表、帳號狀態更新）。
注意事項：
    - 只負責 ORM 操作，不含商業邏輯，不自行 commit（交易由 service 控制）。
    - 查詢預設排除軟刪除資料（deleted_at IS NULL）。
    - email 查詢前一律轉小寫，與寫入規則一致。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.user import User


def get_by_id(db: Session, user_id: int) -> User | None:
    """依主鍵取得使用者；不存在或已軟刪除時回傳 None。"""
    return db.scalar(select(User).where(User.id == user_id, User.deleted_at.is_(None)))


def get_by_email(db: Session, email: str) -> User | None:
    """依 email 取得使用者（不分大小寫）；不存在或已軟刪除時回傳 None。"""
    normalized = email.strip().lower()
    return db.scalar(select(User).where(User.email == normalized, User.deleted_at.is_(None)))


# TODO(JerryChien): list_page(db, *, keyword=None, page, page_size) -> tuple[list[User], int]
