"""ADMIN 登入表單白名單；密碼不修剪、不回填、不序列化至畫面。"""

import re

from pydantic import Field, SecretStr, field_validator

from app.schemas.e_admin import InputModel


class LoginInput(InputModel):
    """僅接受 Email、密碼及受控的返回路徑，不接受角色或 UUID 宣告。"""

    email: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)
    next_path: str = Field(default="/welcome", alias="next", max_length=200)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        """檢查基本 Email 格式；正式帳號正規化仍由 B 負責。"""
        value = value.strip()
        if not re.fullmatch(r"[^@\s\x00-\x1f\x7f]+@[^@\s\x00-\x1f\x7f]+", value):
            raise ValueError("Email 格式不正確")
        return value
