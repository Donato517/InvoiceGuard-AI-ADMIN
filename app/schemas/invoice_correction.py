"""模組名稱：app.schemas.invoice_correction
功能說明：採購在 ADMIN 補正被財務退回的案件——瀏覽器送來的補正內容（只做傳輸層形狀檢查）。
注意事項：
    - 欄位名稱對齊 InvoiceGuard-AI-API 的 InvoiceResubmitRequest／InvoiceVoidRequest；這裡不判斷哪些欄位可以改、
      輪次是否過期、回覆是否完整，也不重算金額——這些規則全部由 API 決定，ADMIN 不複製第二套。
    - 金額與數量一律是字串（StrictStr），瀏覽器若送出數字（float）直接 422，避免精度問題。
    - extra="forbid"：不認得的欄位不轉送，也不能夾帶使用者或部門等自報身分。
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CorrectionItem(_Body):
    """一筆既有明細（只能用上一輪的 item_id，不能新增或刪除）。"""

    item_id: UUID
    item_name: StrictStr = Field(max_length=200)
    quantity: StrictStr | None = Field(default=None, max_length=30)
    unit_price: StrictStr | None = Field(default=None, max_length=30)
    line_amount: StrictStr = Field(max_length=30)


class CorrectionChange(_Body):
    """補正頁的一次欄位修改（依時間順序）；API 據以保存修改歷程。"""

    field_scope: Literal["header", "item"]
    line_no: StrictInt | None = Field(default=None, ge=1, le=50)
    field_name: StrictStr = Field(max_length=40)
    old_value: StrictStr | None = Field(default=None, max_length=200)
    new_value: StrictStr | None = Field(default=None, max_length=200)


class CorrectionResponse(_Body):
    """對一個退件項目的回覆：corrected（已補正）或 appealed（申覆，需說明）。"""

    return_item_id: UUID
    action: Literal["corrected", "appealed"]
    note: StrictStr | None = Field(default=None, max_length=200)


class CorrectionSubmit(_Body):
    """補正頁正式重送：本輪要提交的完整欄位值（含未修改的欄位）、編輯事件與逐項回覆。"""

    idempotency_key: UUID
    expected_round: StrictInt = Field(ge=1)
    invoice_track: StrictStr = Field(max_length=2)
    invoice_number: StrictStr = Field(max_length=8)
    invoice_date: StrictStr = Field(max_length=10)
    seller_tax_id: StrictStr = Field(max_length=8)
    seller_name: StrictStr | None = Field(default=None, max_length=200)
    buyer_tax_id: StrictStr | None = Field(default=None, max_length=8)
    buyer_name: StrictStr | None = Field(default=None, max_length=200)
    tax_type: StrictStr = Field(max_length=20)
    sales_amount: StrictStr = Field(max_length=30)
    tax_amount: StrictStr = Field(max_length=30)
    total_amount: StrictStr = Field(max_length=30)
    total_amount_zh: StrictStr | None = Field(default=None, max_length=200)
    items: tuple[CorrectionItem, ...] = Field(min_length=1, max_length=50)
    changes: tuple[CorrectionChange, ...] = Field(default=(), max_length=500)
    responses: tuple[CorrectionResponse, ...] = Field(min_length=1, max_length=100)


class CorrectionVoid(_Body):
    """作廢被退回的案件；expected_round 防止以過期輪次作廢。"""

    expected_round: StrictInt = Field(ge=1)
    reason: StrictStr | None = Field(default=None, max_length=200)
