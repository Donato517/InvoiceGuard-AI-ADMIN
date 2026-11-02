"""模組名稱：app.schemas.intake
功能說明：ADMIN 發票上傳流程（辨識 → 核對 → 檢核 → 提交）與既有 API 的資料契約——API 回應的解析模型、核對表單、
          對外請求描述、憑證提供者介面與錯誤類型。
注意事項：
    - 欄位名稱與型別對齊 InvoiceGuard-AI-API 的 app/schemas/invoice_upload.py；ADMIN 不複製檢核規則，
      格式與業務檢核一律由 API 回應為準，這裡只負責解析與顯示。
    - 回應模型 extra="ignore"：API 之後新增欄位不會讓 ADMIN 解析失敗；缺少必要欄位則視為上游回應異常。
    - 憑證如何取得（簽章宣告、換發 Token 或其他）尚未核定，因此只定義 CredentialProvider 介面；預設實作拒絕。
    - 金額與數量一律用字串（對齊 API 的 Decimal 字串契約），ADMIN 不做數值運算。
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# ---- API 回應模型（只解析，不檢核）----


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class ApiWarning(_ApiModel):
    field: str
    code: str
    message: str


class RecognizedItemView(_ApiModel):
    line_no: int
    item_name: str | None = None
    quantity: str | None = None
    unit_price: str | None = None
    line_amount: str | None = None


class RecognizedInvoiceView(_ApiModel):
    invoice_track: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    tax_period: str | None = None
    seller_tax_id: str | None = None
    seller_name: str | None = None
    buyer_tax_id: str | None = None
    buyer_name: str | None = None
    tax_type: Literal["taxable", "zero_rated", "tax_exempt"] | None = None
    sales_amount: str | None = None
    tax_amount: str | None = None
    total_amount: str | None = None
    total_amount_zh: str | None = None
    items: tuple[RecognizedItemView, ...] = ()


class RecognitionView(_ApiModel):
    recognition_id: UUID
    result_status: Literal["success", "partial", "failed", "pending"]
    is_triplicate: bool | None = None
    invoice: RecognizedInvoiceView
    printed: dict[str, Any] = {}
    warnings: tuple[ApiWarning, ...] = ()
    recognition: dict[str, Any] = {}
    expires_at: datetime


class MissingFieldView(_ApiModel):
    field: str
    code: str
    message: str
    line_no: int | None = None


class RequiredCheckView(_ApiModel):
    required_complete: bool
    missing: tuple[MissingFieldView, ...] = ()


class WarningCheckView(_ApiModel):
    warnings: tuple[ApiWarning, ...] = ()
    checked_on: date


class DuplicateView(_ApiModel):
    is_duplicate: bool


class ModifiedFieldView(_ApiModel):
    field_scope: str
    line_no: int | None = None
    field_name: str
    ai_original_value: Any = None
    submitted_value: Any = None


class SubmitView(_ApiModel):
    invoice_id: UUID
    status: str
    tax_period: str
    submitted_at: datetime
    warnings: tuple[ApiWarning, ...] = ()
    modified_fields: tuple[ModifiedFieldView, ...] = ()


# ---- 核對表單（使用者看到與編輯的值，全部字串）----


@dataclass(frozen=True)
class IntakeItemForm:
    """一列品名明細；source_line_no 為 AI 明細序號，使用者新增的列為 None。"""

    source_line_no: int | None = None
    item_name: str = ""
    quantity: str = ""
    unit_price: str = ""
    line_amount: str = ""


@dataclass(frozen=True)
class IntakeForm:
    """核對頁的完整表單；recognition_id 沿用辨識時 API 給的值，是提交時 API 認定 AI 原值的依據。"""

    recognition_id: str = ""
    invoice_track: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    seller_tax_id: str = ""
    seller_name: str = ""
    buyer_tax_id: str = ""
    buyer_name: str = ""
    tax_type: str = ""
    sales_amount: str = ""
    tax_amount: str = ""
    total_amount: str = ""
    total_amount_zh: str = ""
    items: tuple[IntakeItemForm, ...] = field(default_factory=tuple)


# ---- 對外請求與憑證 ----


@dataclass(frozen=True)
class OutboundRequest:
    """即將送往 API 的請求描述，供憑證提供者綁定操作、方法、路徑與內容雜湊（不含內容本身）。"""

    operation: Literal["recognize", "check", "submit"]
    method: str
    path: str
    body_sha256: str


class CredentialProvider(Protocol):
    """取得該次 API 請求所需的授權標頭。

    認證方案（簽章宣告／換發 Token／沿用 Bearer）尚未核定；實作必須由已驗證的使用者身分產生憑證，
    不得使用共用管理員帳號、寫死的 Token 或請求中自報的身分。
    """

    def headers(self, request: OutboundRequest) -> dict[str, str]: ...


class IntakeError(Exception):
    """上傳流程錯誤的基底；訊息固定，不含憑證、路徑或內部細節。"""

    message = "發票上傳流程發生錯誤"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        self.public_message = message or self.message


class IntakeNotConfiguredError(IntakeError):
    """認證方案尚未核定或設定；功能 fail closed。"""

    message = "發票上傳功能尚未啟用"


class IntakeFileError(IntakeError):
    """上傳檔案在送往 API 前就不符基本限制（空檔、過大）。"""

    message = "發票檔案不符合上傳限制"


class IntakeApiError(IntakeError):
    """API 回傳錯誤；status／code 來自 API 的標準錯誤格式，details 只含欄位位置與類型。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: tuple[tuple[tuple[str | int, ...], str], ...] = (),
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details


class IntakeTransportError(IntakeError):
    """連線、逾時或回應無法解析。ambiguous=True 表示 API 可能已處理請求，不能當成失敗重送。"""

    message = "無法連線到發票服務，請稍後再試"

    def __init__(self, *, ambiguous: bool, message: str | None = None) -> None:
        super().__init__(message)
        self.ambiguous = ambiguous
