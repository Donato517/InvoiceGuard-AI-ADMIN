"""模組名稱：app.services.invoice_correction_service
功能說明：採購在 ADMIN 補正被財務退回的案件——讀取退件資料、轉送補正與作廢，並把 API 錯誤轉成繁體中文訊息。
注意事項：
    - 不複製 API 的補正規則：欄位白名單（editable_fields）、提交輪次、冪等鍵、圖片退件、明細結構與修改歷程
      全部由 InvoiceGuard-AI-API 判斷並保存；這裡只轉送，並用 API 回傳的 editable_fields 決定畫面哪些欄位可編輯。
    - 以目前登入者自己的 API Session Token 呼叫（由 ApiIntakeClient 帶入），不使用共用帳號；Token 不寫入日誌或訊息。
    - 重送與作廢逾時、結果不明時丟出 ambiguous=True 的 IntakeTransportError；呼叫端必須以同一把冪等鍵重試，不得換鍵重送。
    - 不寫資料庫：狀態、歷程與快照都由 API 在同一交易內保存。
"""

from dataclasses import dataclass, field
from typing import Any

from app.integrations.api_intake_client import ApiIntakeClient
from app.schemas.finance_review import FIELD_LABELS
from app.schemas.intake import (
    FieldRefView,
    IntakeApiError,
    ResubmitView,
    ReturnItemsView,
    ReturnItemView,
    VoidView,
)
from app.schemas.invoice_correction import CorrectionSubmit, CorrectionVoid

HEADER_FIELDS = (
    "invoice_track",
    "invoice_number",
    "invoice_date",
    "seller_tax_id",
    "seller_name",
    "buyer_tax_id",
    "buyer_name",
    "tax_type",
    "sales_amount",
    "tax_amount",
    "total_amount",
    "total_amount_zh",
)
ITEM_FIELDS = ("item_name", "quantity", "unit_price", "line_amount")
TAX_TYPE_OPTIONS = (("taxable", "應稅"), ("zero_rated", "零稅率"), ("tax_exempt", "免稅"))

# API 錯誤碼 → 給採購看的說明；沒有對應的碼沿用 API 的業務訊息（長度已限制）
ERROR_MESSAGES = {
    "RESUBMIT_ROUND_STALE": "案件已有較新的輪次，請重新整理頁面後再操作。",
    "RESUBMIT_IMAGE_REQUIRES_VOID": "這張發票有圖片問題，不能在原案件補正，請終止並重新上傳。",
    "RESUBMIT_FIELD_NOT_ALLOWED": "修改了財務沒有指定的欄位，請還原後再送出。",
    "RESUBMIT_RESPONSES_INCOMPLETE": "請逐項回覆；申覆需填寫說明，已補正的項目必須實際修改。",
    "RESUBMIT_EVENTS_INVALID": "修改紀錄與資料不一致，請重新整理頁面後再補正。",
    "RESUBMIT_ITEMS_STRUCTURE_CHANGED": "補正時不能新增、刪除或重排明細。",
    "RESUBMIT_ORIGIN_UNKNOWN": "無法確認退回來源的審核節點，請聯絡財務人員。",
    "RETURN_ITEMS_MISSING": "這張發票沒有可補正的退件項目，請聯絡財務人員。",
    "INVOICE_DUPLICATED": "此發票已存在於系統中，不能重複送出。",
    "INVOICE_NOT_RETURNED": "這張發票已不是待補正狀態（可能已重新送出或已終止）。",
    "IDEMPOTENCY_KEY_REUSED": "內容已變更，請重新按送出。",
}
LOGIN_AGAIN = "授權失敗，請重新登入後再試。"
UNAVAILABLE = "發票服務暫時發生錯誤，請稍後再試。"
# API 對「案件不存在、不是本人提交、不屬於目前選取部門」回應相同（NotFoundError），ADMIN 無法也不應區分，
# 所以只列出可能性，不把任何一種當成已確定原因，也不透露其他使用者或部門的案件資訊
NOT_FOUND = (
    "找不到這張發票，或目前登入帳號無法存取該案件。"
    "請確認發票連結及登入帳號是否正確。若案件屬於其他部門，或您不是原提交者，也可能無法開啟。"
    "若確認資料無誤仍無法存取，請聯絡管理員協助查詢。"
)


def error_message(error: IntakeApiError) -> str:
    """API 錯誤 → 繁體中文訊息。401／403 與 5xx 用固定文字，不顯示上游細節。"""
    if error.status in (401, 403):
        return LOGIN_AGAIN
    if error.status >= 500:
        return UNAVAILABLE
    if error.code in ERROR_MESSAGES:
        return ERROR_MESSAGES[error.code]
    if error.status == 404:
        return NOT_FOUND
    if error.status == 422:
        return "欄位格式不正確，請檢查後再送出。"
    return error.public_message


FIELD_LABELS_VIEW = FIELD_LABELS  # 畫面顯示用的欄位中文名稱（與財務審核頁相同）


def field_label(name: str) -> str:
    return FIELD_LABELS.get(name, name)


def editable_key(scope: str, item_id: Any, name: str) -> str:
    """可編輯欄位的鍵：「範圍|item_id|欄位名」；表頭欄位的 item_id 為空。"""
    return f"{scope}|{item_id or ''}|{name}"


def _ref_key(ref: FieldRefView) -> str:
    return editable_key(ref.field_scope, ref.item_id, ref.field_name)


@dataclass(frozen=True)
class CorrectionPage:
    """補正頁的畫面資料。mode：form 可補正／void 圖片退件只能終止／blocked 目前不能在這裡處理。"""

    info: ReturnItemsView
    mode: str
    editable: frozenset[str]
    field_items: tuple[ReturnItemView, ...] = field(default=())
    other_items: tuple[ReturnItemView, ...] = field(default=())
    blocked_reason: str = ""


def build_page(info: ReturnItemsView) -> CorrectionPage:
    """依 API 的 can_resubmit／requires_void 決定模式；可編輯欄位完全取自 API 的 editable_fields。"""
    field_items = tuple(i for i in info.return_items if i.item_kind == "field")
    other_items = tuple(i for i in info.return_items if i.item_kind != "field")
    editable = frozenset(_ref_key(ref) for ref in info.editable_fields)
    if info.requires_void:
        mode, reason = "void", ""
    elif info.can_resubmit:
        mode, reason = "form", ""
    else:
        mode, reason = "blocked", info.resubmit_blocked_reason or "目前不能在這裡補正"
    return CorrectionPage(info, mode, editable, field_items, other_items, reason)


def load_page(client: ApiIntakeClient, invoice_id: str) -> CorrectionPage:
    """讀取退件項目與上一輪提交值（只有原提交者讀得到；其餘 API 回 404）。"""
    return build_page(client.get_return_items(invoice_id))


def submit(client: ApiIntakeClient, invoice_id: str, payload: CorrectionSubmit) -> ResubmitView:
    """正式重送：原樣轉送欄位值、編輯事件與逐項回覆，所有規則由 API 判斷。"""
    return client.resubmit(invoice_id, payload.model_dump(mode="json"))


def void(client: ApiIntakeClient, invoice_id: str, payload: CorrectionVoid) -> VoidView:
    """終止（作廢）被退回的案件，呼叫 API /void；不直接寫資料庫。"""
    body = payload.model_dump(mode="json", exclude_none=True)
    return client.void(invoice_id, body)
