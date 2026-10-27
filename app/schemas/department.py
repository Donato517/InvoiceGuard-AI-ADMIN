"""模組名稱：app.schemas.department
功能說明：既有 v1.3 部門資料契約，不定義或變更資料表。
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class Department(BaseModel):
    """部門列表與編輯使用的資料。"""

    department_id: UUID
    department_code: str
    department_name: str
    is_active: bool
    created_at: datetime | None
    updated_at: datetime | None
