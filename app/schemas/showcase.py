"""模組名稱：app.schemas.showcase；功能說明：供應商範例、逐欄真值與測試核對契約。"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

CODE = "supplier.showcase"
CATALOG_CODE = "show_case"
ACTIVATE_CODE = "supplier.showcase-activation"
MAX_BYTES = 4 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_ITEM_LINES = 50
MAX_TRUTH_FIELDS = 12 + MAX_ITEM_LINES * 4


class SearchInput(BaseModel):
    """受控搜尋條件；狀態與用途不得變成任意 SQL。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    q: str = Field(default="", max_length=120)
    page: int = Field(default=1, ge=1, le=10000)
    layout: str = Field(default="", max_length=120)
    writing: str = Field(default="", max_length=120)
    status: Literal["all", "active", "inactive"] = "all"
    kind: Literal["all", "triplicate"] = "all"
    supplier_id: UUID | None = None


class Truth(BaseModel):
    """票面答案；未知與空白不轉成推算答案。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    path: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.\[\]-]*$")
    state: Literal["readable", "blank", "unreadable", "not_applicable", "unconfirmed"]
    text: str = Field(default="", max_length=2000)


class SampleInput(BaseModel):
    """每次存檔建立不可變修訂，供應商由伺服器解析穩定 ID。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)
    supplier_name: str = Field(min_length=1, max_length=120)
    tax_id: str = Field(pattern=r"^(?:[0-9]{8})?$")
    master_supplier_id: UUID | None = None
    kind: Literal["triplicate"] = "triplicate"
    layout: str = Field(min_length=1, max_length=120)
    writing: str = Field(default="", max_length=120)
    reason: str = Field(min_length=1, max_length=500)
    copy_type: Literal["unknown", "deduction", "receipt", "stub"] = "unknown"
    valid_from: date | None = None
    valid_to: date | None = None
    key_fields: list[str] = Field(default_factory=list, max_length=MAX_TRUTH_FIELDS)
    truths: list[Truth] = Field(default_factory=list, max_length=MAX_TRUTH_FIELDS)

    @model_validator(mode="after")
    def require_legacy_tax_id(self) -> "SampleInput":
        """未連主檔的舊流程仍以八碼統編辨識供應商。"""
        if not self.tax_id and self.master_supplier_id is None:
            raise ValueError("請填寫八碼供應商統編")
        return self


def validate_truths(data: SampleInput, *, activation: bool = False) -> None:
    """檢查重複路徑與空值；啟用須明確核對至少一個可讀欄位。"""
    paths = [item.path for item in data.truths]
    if data.valid_from and data.valid_to and data.valid_from > data.valid_to:
        raise ValueError("適用開始日期不得晚於結束日期")
    if len(data.key_fields) != len(set(data.key_fields)) or not set(data.key_fields) <= set(paths):
        raise ValueError("關鍵欄位必須對應不重複的票面欄位")
    if activation and any(
        item.path in data.key_fields and item.state != "readable" for item in data.truths
    ):
        raise ValueError("啟用前請人工確認所有選定關鍵欄位的票面原文")
    if len(paths) != len(set(paths)):
        raise ValueError("欄位路徑不可重複")
    for item in data.truths:
        if item.state == "unconfirmed":
            if activation and item.text:
                raise ValueError("請先核對或清除尚未確認的辨識值")
            continue
        if (item.state == "readable") != bool(item.text):
            raise ValueError("可讀欄位需填票面原文，空白／不可讀／不適用不可填答案")
    if activation and not any(item.state == "readable" for item in data.truths):
        raise ValueError("啟用前至少確認一個可讀欄位；其他欄位狀態需逐一核對")
