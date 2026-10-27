"""
模組名稱：app.schemas.account
功能說明：開發者帳號（invoiceguard_identity.user_accounts）的查詢結果與新增／修改輸入契約。
注意事項：
    - 雲端帳號 API 與本機 remote client 共用；查詢結果不含 password_hash。
    - 密碼只存在新增／修改輸入，由 service 雜湊或驗證，不回傳、不記錄。
"""

from uuid import UUID

from pydantic import Field

from app.schemas.common import ContractModel


class AccountSummary(ContractModel):
    """帳號清單與單筆結果的必要欄位。"""

    user_id: UUID
    email: str = Field(max_length=320)
    account_status: str = Field(max_length=20)


class AccountCreate(ContractModel):
    """新增帳號輸入：Email 與初始密碼。"""

    email: str = Field(max_length=320)
    password: str = Field(max_length=256, repr=False)


class AccountUpdate(ContractModel):
    """修改自己的帳號：需目前密碼；new_password 為 None 或空字串表示不變更密碼。"""

    email: str = Field(max_length=320)
    current_password: str = Field(max_length=256, repr=False)
    new_password: str | None = Field(default=None, max_length=256, repr=False)
