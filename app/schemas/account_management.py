"""模組名稱：app.schemas.account_management
功能說明：正式帳號多部門設定資料契約，與登入及 dev 帳號契約分離。
"""

from uuid import UUID

from pydantic import BaseModel

from app.schemas.department import Department


class AccountDepartmentSettings(BaseModel):
    """帳號已選部門、主要部門與資料庫部門選項。"""

    departments: list[Department]
    selected_ids: list[UUID]
    primary_department_id: UUID | None
