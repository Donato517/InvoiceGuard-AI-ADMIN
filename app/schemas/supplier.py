"""模組名稱：app.schemas.supplier；功能說明：供應商試作欄位與驗證。"""

from pydantic import BaseModel, Field, field_validator, model_validator

PAYMENT_TERMS = {
    "immediate": "現付",
    "net": "帳期",
    "monthly": "月結",
    "prepaid": "預付",
    "installment": "分期",
}
PAYMENT_BASES = {
    "invoice": "發票日",
    "acceptance": "驗收日",
    "received": "收件日",
    "month_end": "月底",
    "contract": "依合約",
}
PAYMENT_METHODS = {"transfer": "匯款", "check": "支票", "cash": "現金", "other": "其他"}
CREDIT_RATINGS = {
    "unassessed": "未評估",
    "A": "A 級（模擬低風險）",
    "B": "B 級（模擬一般風險）",
    "C": "C 級（模擬需加強查核）",
}


class SupplierInput(BaseModel):
    """接收表單；條件僅供試作，不計算正式到期日。"""

    supplier_name: str = Field(min_length=1, max_length=120)
    country_code: str = Field(min_length=2, max_length=2)
    tax_id: str = Field(default="", max_length=40)
    aliases: str = Field(default="", max_length=600)
    contact_name: str = Field(default="", max_length=80)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=200)
    address: str = Field(default="", max_length=300)
    payment_terms: str
    payment_basis: str
    payment_days: int = Field(ge=0, le=3650)
    payment_method: str
    currency: str = Field(min_length=3, max_length=3)
    notes: str = Field(default="", max_length=1500)
    credit_rating: str = "unassessed"
    delivery_terms: str = Field(default="", max_length=300)

    @field_validator("*", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        """清除字串兩側空白。"""
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_rules(self) -> "SupplierInput":
        """驗證國別、編號與付款選項。"""
        self.country_code = self.country_code.upper()
        self.currency = self.currency.upper()
        self.tax_id = self.tax_id.upper()
        if self.credit_rating not in CREDIT_RATINGS:
            raise ValueError("請選擇有效的供應商徵信等級")
        if not self.country_code.isascii() or not self.country_code.isalpha():
            raise ValueError("國家／地區代碼請填兩碼英文字母")
        if not self.currency.isascii() or not self.currency.isalpha():
            raise ValueError("幣別請填三碼英文字母")
        if self.country_code == "TW" and (
            len(self.tax_id) != 8 or not self.tax_id.isascii() or not self.tax_id.isdigit()
        ):
            raise ValueError("台灣供應商統編必須為八碼數字（試作只檢查格式）")
        if self.email and (
            self.email.count("@") != 1
            or any(x.isspace() for x in self.email)
            or not all(self.email.split("@"))
        ):
            raise ValueError("Email 格式不正確")
        if (
            self.payment_terms not in PAYMENT_TERMS
            or self.payment_basis not in PAYMENT_BASES
            or self.payment_method not in PAYMENT_METHODS
        ):
            raise ValueError("請選擇有效的付款條件、起算基準及付款方式")
        if self.payment_terms == "immediate" and self.payment_days != 0:
            raise ValueError("現付的付款天數請填 0")
        if self.payment_terms in {"prepaid", "installment"} and self.payment_basis != "contract":
            raise ValueError("預付或分期請選擇依合約，並在備註說明約定")
        if self.payment_terms in {"prepaid", "installment"} and not self.notes:
            raise ValueError("預付或分期請在備註說明約定")
        aliases = list(
            dict.fromkeys(line.strip() for line in self.aliases.splitlines() if line.strip())
        )
        if len(aliases) > 10 or any(len(line) > 60 for line in aliases):
            raise ValueError("最多 10 個別名，每個不超過 60 字")
        self.aliases = "\n".join(aliases)
        return self
