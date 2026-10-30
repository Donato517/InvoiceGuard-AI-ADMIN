"""
模組名稱：app.schemas.finance_review
功能說明：財務審核（2-4）的資料契約——審核中的請款單、欄位確認、檢核提醒與狀態異動。
注意事項：
    - 欄位對應 Schema v1.5 的 invoices、invoice_field_reviews、invoice_status_history。
    - 一律為不可變物件；審核動作回傳新物件，由 service 交給 repository 在同一交易寫入。
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

# ---- 狀態 ----
# 2026-10-30 團隊決定只留財務一層審核，不使用單位核准與會計核准兩層。
# C 的 API 目前送出（含退回後重送）寫入 pending_department_approval，日後可能改為
# pending_accounting_review；兩個狀態都保留，皆視為「待財務審核」，C 修改前後都能運作。

STATUS_SUBMITTED = "pending_department_approval"
STATUS_PENDING_REVIEW = "pending_accounting_review"
STATUS_APPROVED = "approved"
STATUS_RETURNED = "returned_to_applicant"
STATUS_REJECTED = "rejected"

REVIEWABLE_STATUSES = frozenset({STATUS_SUBMITTED, STATUS_PENDING_REVIEW})

STATUS_LABELS = {
    STATUS_SUBMITTED: "待財務審核",
    STATUS_PENDING_REVIEW: "待財務審核",
    STATUS_APPROVED: "已核准",
    STATUS_RETURNED: "退回補正",
    STATUS_REJECTED: "已駁回",
}

# 畫面顯示用的欄位名稱；刪除的 AI 明細（deleted_item_N）另由 service 組字
FIELD_LABELS = {
    "invoice_track": "字軌",
    "invoice_number": "發票號碼",
    "invoice_date": "發票日期",
    "seller_tax_id": "賣方統編",
    "seller_name": "賣方名稱",
    "buyer_tax_id": "買方統編",
    "buyer_name": "買方名稱",
    "sales_amount": "銷售額",
    "tax_amount": "稅額",
    "total_amount": "總計",
    "total_amount_zh": "總計（國字）",
    "tax_type": "課稅別",
    "item_name": "品名",
    "quantity": "數量",
    "unit_price": "單價",
    "line_amount": "金額",
    "attachment": "附件",
}

# 財務審核時填寫的會計欄位
TAX_TYPES = {"taxable": "1 應稅", "zero_rated": "2 零稅率", "tax_exempt": "3 免稅"}
# 會計科目 → (名稱, 預設扣抵代號)；選科目時帶出扣抵代號，財務仍可調整。
# TODO(StacieChen): 示範清單；會計科目主檔進 Schema 後改讀資料表（見 F_Schema補充建議_v1.5.md §7）
ACCOUNT_CODES = {
    "5601": ("辦公用品費", "1"),
    "5602": ("什支", "1"),
    "5605": ("修繕費", "1"),
    "5611": ("交際費", "3"),
    "5612": ("職工福利", "3"),
    "1401": ("辦公設備", "2"),
    "1421": ("電腦設備", "2"),
}

DEDUCTION_CODES = {
    "1": "1 進貨及費用",
    "2": "2 固定資產",
    "3": "3 不得扣抵之進貨及費用",
    "4": "4 不得扣抵之固定資產",
}


@dataclass(frozen=True)
class FieldReview:
    """一個「AI 原值 → 送出值」不同的欄位，財務須逐欄確認。

    Attributes:
        field_scope: header 或 item。
        field_name: 欄位名稱。
        item_id: 明細欄位所屬明細；表頭為 None。
        ai_original_value: AI 辨識的標準值。
        submitted_value: 申請人送出的標準值。
        is_checked: 財務是否已確認。
    """

    field_scope: str
    field_name: str
    item_id: str | None
    ai_original_value: Any
    submitted_value: Any
    is_checked: bool = False
    checked_by: str | None = None
    checked_at: datetime | None = None
    review_comment: str | None = None


@dataclass(frozen=True)
class ChangeTrail:
    """一個欄位在一輪送出中的逐次修改軌跡（來自 invoice_changes，僅供顯示）。

    Attributes:
        submission_round: 第幾次送出。
        item_id: 明細欄位所屬明細；表頭為 None。
        field_name: 欄位名稱；刪除的 AI 明細為 deleted_item_{AI 明細序號}。
        values: 原值與每次修改後的值，例如 ("6030", "6330", "6300")。
        change_sources: 每次修改的來源（user／ai／system）。
    """

    submission_round: int
    item_id: str | None
    field_name: str
    values: tuple[Any, ...]
    change_sources: tuple[str, ...]

    @property
    def change_count(self) -> int:
        """修改次數。"""
        return len(self.values) - 1


@dataclass(frozen=True)
class ReviewWarning:
    """C 送出時的檢核提醒（不擋送出），財務審核時須逐項確認。

    Attributes:
        field: 欄位名稱。
        code: C 的固定代碼，例如 tax_amount_mismatch。
        message: 給使用者看的中文說明。
        acknowledged_by: 確認的財務人員；None 代表尚未處理。
        note: 確認備註，確認時必填。
    """

    field: str
    code: str
    message: str
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    note: str | None = None


@dataclass(frozen=True)
class ReturnItem:
    """退回補正的一個項目。

    Attributes:
        field: 要補正的欄位或附件。
        reason: 要申請人補什麼。
        item_id: 明細欄位所屬明細；表頭或附件為 None。
    """

    field: str
    reason: str
    item_id: str | None = None


@dataclass(frozen=True)
class ReviewCase:
    """財務審核中的一張請款單。

    Attributes:
        invoice_id: 請款單 UUID。
        status: 目前狀態。
        submission_round: 第幾次送出。
        submitted_by: 送出者 user_id，用來防止自己核准自己的單。
        tax_type: 課稅別；財務可修改。
        deduction_code: 扣抵代號 1～4；財務審核時填寫。
    """

    invoice_id: str
    status: str
    submission_round: int
    submitted_by: str | None
    invoice_track: str | None
    invoice_number: str | None
    seller_tax_id: str | None
    sales_amount: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None
    tax_type: str | None
    deduction_code: str | None
    field_reviews: tuple[FieldReview, ...] = ()
    warnings: tuple[ReviewWarning, ...] = ()


@dataclass(frozen=True)
class ReviewBlocker:
    """不能核准的原因；code 給程式判斷，message 給使用者閱讀。"""

    code: str
    message: str


@dataclass(frozen=True)
class StatusChange:
    """一次狀態異動，對應 invoice_status_history 一列。

    Attributes:
        expected_status: 寫入時必須仍是此狀態，否則代表他人已先處理。
        reason: 寫入 invoice_status_history.reason；退回時只放沒有對應
            invoice_field_reviews 列的項目（「欄位：原因」逐行），全部有對應時為 None。
        return_items: 退回時的逐項清單。
        field_review_updates: 退回時要同交易更新的 invoice_field_reviews
            （review_comment 寫入原因並維持未確認，申請人由 v_invoice_return_fields 讀取）。
    """

    invoice_id: str
    old_status: str
    new_status: str
    reason: str | None
    changed_by: str
    changed_at: datetime
    return_items: tuple[ReturnItem, ...] = field(default=())
    field_review_updates: tuple[FieldReview, ...] = field(default=())

    @property
    def expected_status(self) -> str:
        """寫入時的併發條件：資料庫中的狀態必須仍等於 old_status。"""
        return self.old_status
