"""模組名稱：app.schemas.case
功能說明：ADMIN 讀取前台已提交真實發票案件（Schema v1.8 的 invoices 與子表）的唯讀資料契約。
注意事項：
    - 欄位名稱與 public.invoices／invoice_items／invoice_files／invoice_changes／invoice_status_history／
      invoice_field_reviews／ai_recognition_logs 一致，不自行發明欄位；金額以 Decimal 保留資料庫位數。
    - 狀態為 v1.4 起的 9 種簽核狀態，與 ADMIN 既有展示發票（INVOICE_STATUS_LABEL／finance_review）不同。
    - 檔案物件路徑（bucket_name、storage_path）只存在 CaseFileLocation，不放進任何會傳到畫面的資料。
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

# 9 種簽核狀態（invoices_status_chk）；draft 為未提交草稿，ADMIN 不列出。
CASE_STATUS_LABELS: dict[str, str] = {
    "draft": "草稿",
    "pending_department_approval": "待單位核准",
    "pending_accounting_review": "待會計覆核",
    "pending_accounting_approval": "待會計核准",
    "approved": "已核准",
    "returned_to_applicant": "退回申請人",
    "returned_to_accounting": "退回會計",
    "rejected": "駁回",
    "voided": "作廢",
}
LISTED_STATUSES = frozenset(CASE_STATUS_LABELS) - {"draft"}

TAX_TYPE_LABELS: dict[str, str] = {
    "taxable": "應稅",
    "zero_rated": "零稅率",
    "tax_exempt": "免稅",
}

CHANGE_SOURCE_LABELS: dict[str, str] = {"user": "使用者", "ai": "AI", "system": "系統"}

IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "application/pdf"})


class CaseError(Exception):
    """案件讀取失敗的基底；訊息固定，不含 SQL、路徑或連線資訊。

    code 供畫面區分原圖失敗的三類：no_image（沒有可顯示的原圖）、image_unreadable（原圖無法讀取）、
    temporary（暫時性錯誤）；其他錯誤沿用各自的固定代碼。
    """

    status = 400
    code = "case_error"
    message = "案件資料讀取失敗"

    def __init__(self) -> None:
        super().__init__(self.message)


class CaseNotFoundError(CaseError):
    """案件不存在、或不在目前身分可讀範圍（兩者不區分，避免洩漏範圍外資料）。"""

    status = 404
    code = "not_found"
    message = "找不到指定案件"


class CaseNoImageError(CaseError):
    """案件存在，但沒有目前有效的影像紀錄，或影像類型不被允許顯示。"""

    status = 404
    code = "no_image"
    message = "此案件沒有可顯示的原圖"


class CaseFileMissingError(CaseError):
    """有影像紀錄，但私有 Storage 中找不到該物件。"""

    status = 404
    code = "image_unreadable"
    message = "原圖無法讀取，檔案可能已被移除，請聯絡系統管理員"


class CaseStorageError(CaseError):
    """Storage 拒絕、未設定或類型被拒；重試無法解決，需管理員處理。不得顯示假成功。"""

    status = 502
    code = "image_unreadable"
    message = "原圖無法讀取，請聯絡系統管理員"


class CaseStorageUnavailableError(CaseError):
    """Storage 暫時連不上或暫時失敗；稍後重試可能恢復。"""

    status = 503
    code = "temporary"
    message = "暫時無法讀取原圖，請稍後重試"


class CaseUnavailableError(CaseError):
    """資料庫未設定或查詢失敗。"""

    status = 503
    code = "temporary"
    message = "案件資料目前無法讀取，請稍後再試"


class CaseDataInvalidError(CaseError):
    """已保存的資料不符預期結構；不略過壞資料。"""

    status = 500
    code = "data_invalid"
    message = "已保存的案件資料格式異常"


@dataclass(frozen=True)
class CaseScope:
    """目前身分可讀的案件範圍。kind=none 為拒絕；department 只含該送出部門；all 為全部（需另行核定）。"""

    kind: str = "none"
    department_id: UUID | None = None

    def allows(self, case_department_id: UUID | None) -> bool:
        if self.kind == "all":
            return True
        if self.kind == "department":
            return (
                self.department_id is not None
                and case_department_id is not None
                and self.department_id == case_department_id
            )
        return False


@dataclass(frozen=True)
class CaseSummary:
    invoice_id: UUID
    invoice_track: str | None
    invoice_number: str | None
    invoice_date: date | None
    seller_name: str | None
    total_amount: Decimal | None
    status: str
    submission_round: int
    submitted_by: UUID | None
    submitter_name: str | None
    department_id: UUID | None
    department_name: str | None
    submitted_at: datetime | None


@dataclass(frozen=True)
class CaseItem:
    item_id: UUID
    line_no: int
    item_name: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    line_amount: Decimal | None


@dataclass(frozen=True)
class CaseHeader(CaseSummary):
    tax_period: str | None = None
    seller_tax_id: str | None = None
    buyer_tax_id: str | None = None
    buyer_name: str | None = None
    sales_amount: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount_zh: str | None = None
    tax_type: str | None = None
    invoice_kind: str | None = None
    validation_results: Any = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class CaseFileInfo:
    """影像中繼資料（可顯示）；不含 bucket 與物件路徑。"""

    file_id: UUID
    original_filename: str | None
    mime_type: str | None
    size_bytes: int | None
    version_no: int
    uploaded_at: datetime | None


@dataclass(frozen=True)
class CaseFileLocation:
    """目前有效影像的物件位置；僅供伺服器端讀 Storage，不傳到畫面。"""

    file_id: UUID
    bucket_name: str
    storage_path: str
    mime_type: str | None


@dataclass(frozen=True)
class CaseFieldReview:
    """送出時 AI 原值與使用者送出值不同的欄位（invoice_field_reviews）。"""

    submission_round: int
    field_scope: str
    line_no: int | None
    field_name: str
    ai_original_value: Any
    submitted_value: Any
    is_checked: bool
    checked_at: datetime | None
    review_comment: str | None


@dataclass(frozen=True)
class CaseChange:
    """逐次修改紀錄（invoice_changes），含改了又改回。"""

    change_id: UUID
    submission_round: int
    line_no: int | None
    field_name: str
    old_value: Any
    new_value: Any
    change_source: str
    changed_by: UUID | None
    changer_name: str | None
    changed_at: datetime


@dataclass(frozen=True)
class CaseStatusEntry:
    history_id: UUID
    old_status: str | None
    new_status: str
    reason: str | None
    changed_by: UUID | None
    changer_name: str | None
    changed_at: datetime


@dataclass(frozen=True)
class CaseRecognition:
    """最近一次連到此案件的 AI 辨識紀錄（ai_recognition_logs）；不含 raw_response。"""

    recognition_id: UUID
    ai_provider: str
    model_name: str | None
    result_status: str
    confidence_score: Decimal | None
    extracted_fields: Any
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class CaseWarning:
    field: str
    code: str
    message: str


@dataclass(frozen=True)
class CaseDetail:
    header: CaseHeader
    items: tuple[CaseItem, ...] = ()
    file: CaseFileInfo | None = None
    field_reviews: tuple[CaseFieldReview, ...] = ()
    changes: tuple[CaseChange, ...] = ()
    status_history: tuple[CaseStatusEntry, ...] = ()
    recognition: CaseRecognition | None = None
    warnings: tuple[CaseWarning, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CasePage:
    items: tuple[CaseSummary, ...]
    total: int
    page: int
    page_size: int
