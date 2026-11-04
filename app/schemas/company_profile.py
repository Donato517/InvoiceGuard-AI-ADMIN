"""模組名稱：app.schemas.company_profile；功能說明：本公司基本資料的表單欄位與格式驗證；名稱、統一編號、稅籍編號必填，其餘選填僅記錄顯示。"""

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.tw_postal_codes import POSTAL_CODES


class CompanyProfileInput(BaseModel):
    """公司基本資料表單；統編檢查碼由 service 驗證，稅籍編號沒有公開檢查碼只驗格式。"""

    company_name: str = Field(min_length=1, max_length=120)
    ban: str = Field(pattern=r"^[0-9]{8}$")
    tax_reg_no: str = Field(pattern=r"^[0-9]{9}$")
    # 以下選填，只記錄與顯示，系統邏輯不使用
    representative_name: str = Field(default="", max_length=80)
    # 營業地址：縣市與鄉鎮市區從清單選，郵遞區號由程式依兩者帶出（不接受表單自填）
    business_zip_code: str = ""
    business_city: str = Field(default="", max_length=10)
    business_district: str = Field(default="", max_length=20)
    business_street: str = Field(default="", max_length=200)
    tax_office: str = Field(default="", max_length=80)
    contact_name: str = Field(default="", max_length=80)
    contact_phone: str = Field(default="", max_length=40)
    contact_email: str = Field(default="", max_length=200, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$")

    @field_validator("*", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        """去除前後空白（含全形空白），避免複製貼上帶入空白。"""
        return value.strip().strip("　") if isinstance(value, str) else value

    @model_validator(mode="after")
    def fill_zip_code(self) -> "CompanyProfileInput":
        """縣市與鄉鎮市區須同時選或同時不選，且須在中華郵政清單內；郵遞區號一律由清單帶出。"""
        if not self.business_city and not self.business_district:
            self.business_zip_code = ""
            return self
        zip_code = POSTAL_CODES.get(self.business_city, {}).get(self.business_district)
        if zip_code is None:
            raise ValueError("營業地址的縣市與鄉鎮市區須從清單選擇")
        self.business_zip_code = zip_code
        return self
