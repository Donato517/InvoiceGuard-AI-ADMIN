"""
模組名稱：app.schemas.procurement_case
功能說明：採購案件（2-5）的資料契約——案件列表、退回補正項目、逐項回覆與重送計畫。
注意事項：
    - 欄位對應 Schema v1.5 的 invoices、invoice_status_history。
    - 狀態字串與財務審核（app.schemas.finance_review）相同；兩邊合併後再整併為共用定義。
    - 實際重送（寫入新版本、submission_round +1）由 C 的 service 執行，本模組只產生重送計畫。
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

# ---- 狀態 ----
# 只留財務一層審核；C 送出與重送目前寫入 pending_department_approval，日後可能改為
# pending_accounting_review，兩者在採購端都顯示為「財務審核中」。

STATUS_DRAFT = "draft"
STATUS_SUBMITTED = "pending_department_approval"
STATUS_PENDING_REVIEW = "pending_accounting_review"
STATUS_APPROVED = "approved"
STATUS_RETURNED = "returned_to_applicant"
STATUS_REJECTED = "rejected"

# 採購只能修改草稿與退回補正的案件，其他狀態唯讀
EDITABLE_STATUSES = frozenset({STATUS_DRAFT, STATUS_RETURNED})

# 重送後的狀態，與 C 的 API 送出時相同
RESUBMIT_STATUS = STATUS_SUBMITTED

STATUS_LABELS = {
    STATUS_DRAFT: "草稿",
    STATUS_SUBMITTED: "財務審核中",
    STATUS_PENDING_REVIEW: "財務審核中",
    STATUS_APPROVED: "已核准",
    STATUS_RETURNED: "待補正",
    STATUS_REJECTED: "已駁回",
}

# 逐項回覆的處理方式
ACTION_CORRECTED = "corrected"
ACTION_APPEALED = "appealed"
RESPONSE_ACTIONS = {ACTION_CORRECTED: "已補正", ACTION_APPEALED: "申覆（不修改）"}

# 補正附件時放入 changed_fields 的代表名稱
ATTACHMENT_FIELD = "attachment"


@dataclass(frozen=True)
class CaseScope:
    """目前使用者可看到的範圍：以目前選取的部門為準，不聯集多部門。

    Attributes:
        user_id: 目前使用者。
        department_id: 目前選取的部門。
    """

    user_id: str
    department_id: str


@dataclass(frozen=True)
class CaseFilter:
    """列表篩選條件；None 代表不篩選。

    Attributes:
        statuses: 只列這些狀態。
        date_from: 發票日期起（含）。
        date_to: 發票日期迄（含）。
        keyword: 比對發票號碼、賣方名稱或賣方統編。
    """

    statuses: frozenset[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    keyword: str | None = None


@dataclass(frozen=True)
class CaseSummary:
    """採購列表的一列。

    Attributes:
        invoice_id: 請款單 UUID。
        status_label: 給使用者看的狀態。
        is_editable: 是否可修改（草稿或待補正）。
    """

    invoice_id: str
    invoice_no: str
    invoice_date: date | None
    seller_name: str | None
    total_amount: Decimal | None
    status: str
    status_label: str
    is_editable: bool
    submission_round: int
    submitted_at: datetime | None


@dataclass(frozen=True)
class ReturnedItem:
    """一個要補正的項目。

    Attributes:
        field: 欄位名稱，或 attachment（附件）、其他。
        reason: 財務寫的補正原因。
        item_id: 明細欄位所屬明細；表頭、附件為 None。
    """

    field: str
    reason: str
    item_id: str | None = None

    @property
    def key(self) -> tuple[str, str | None]:
        """比對回覆用的鍵：同名欄位可能出現在不同明細。"""
        return (self.field, self.item_id)


@dataclass(frozen=True)
class ReturnRequest:
    """最近一次退回補正的內容。

    Attributes:
        items: 要補正的項目；先列欄位確認（v_invoice_return_fields），再列其他項目。
        returned_by: 退回的財務人員。
    """

    invoice_id: str
    items: tuple[ReturnedItem, ...]
    returned_by: str | None
    returned_at: datetime | None


@dataclass(frozen=True)
class CorrectionResponse:
    """申請人對一個退回項目的回覆。

    Attributes:
        field: 退回項目的欄位。
        action: corrected（已補正）或 appealed（申覆，不修改並說明理由）。
        note: 說明；申覆時必填。
        item_id: 明細欄位所屬明細；表頭、附件為 None。
    """

    field: str
    action: str
    note: str | None = None
    item_id: str | None = None

    @property
    def key(self) -> tuple[str, str | None]:
        """比對退回項目用的鍵。"""
        return (self.field, self.item_id)


@dataclass(frozen=True)
class ResubmitBlocker:
    """不能重送的原因；code 給程式判斷，message 給使用者閱讀。"""

    code: str
    message: str


@dataclass(frozen=True)
class ResubmitPlan:
    """通過檢查的重送內容，交給 C 的 service 寫入。

    Attributes:
        expected_status: 寫入時狀態必須仍是此值（防止重複送出）。
        next_round: 重送後的 submission_round。
        target_status: 重送後的狀態。
        reason: 寫入 invoice_status_history.reason 的逐項回覆摘要。
    """

    invoice_id: str
    expected_status: str
    next_round: int
    target_status: str
    responses: tuple[CorrectionResponse, ...]
    reason: str
