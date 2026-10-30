"""
模組名稱：app.services.procurement_case_service
功能說明：採購案件（2-5）的業務規則——部門範圍列表、可否修改、讀取退回項目與重送前逐項檢查。
相依模組：app.schemas.procurement_case、app.services.finance_review_service（欄位中文名稱）
注意事項：
    - 本模組不存取資料庫；資料表欄位對應集中在 summarize_case() 與 latest_return()。
    - 只有草稿與待補正可修改，其他狀態唯讀。
    - 重送的實際寫入（新版本、submission_round +1、invoice_changes）由 C 的 service 執行；
      本模組產生 ResubmitPlan，寫入時須以「狀態仍等於 expected_status」為條件，避免重複送出。
    - 退回項目沿用 Schema v1.5 機制：修改過的欄位讀 v_invoice_return_fields 的 review_comment；
      未修改過的欄位、附件等讀該次 invoice_status_history.reason 的「欄位：原因」逐行文字
      （格式見 finance_review_service.return_to_applicant）。
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.schemas.procurement_case import (
    ACTION_APPEALED,
    ACTION_CORRECTED,
    EDITABLE_STATUSES,
    RESPONSE_ACTIONS,
    RESUBMIT_STATUS,
    STATUS_LABELS,
    STATUS_RETURNED,
    CaseFilter,
    CaseScope,
    CaseSummary,
    CorrectionResponse,
    ResubmitBlocker,
    ResubmitPlan,
    ReturnedItem,
    ReturnRequest,
)
from app.services.finance_review_service import field_label

# 退回原因中沒有「欄位：」前綴的行
UNSPECIFIED_FIELD = "其他"


class ProcurementCaseError(Exception):
    """採購端動作不合規則；blockers 列出所有原因，供畫面逐項顯示。"""

    def __init__(self, blockers: list[ResubmitBlocker]) -> None:
        super().__init__("；".join(blocker.message for blocker in blockers))
        self.blockers = blockers


# ---- 列表 ----


def can_edit(status: str) -> bool:
    """採購是否可修改此狀態的案件。

    Args:
        status: invoices.status。

    Returns:
        草稿或待補正為 True，其他唯讀。
    """
    return status in EDITABLE_STATUSES


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _to_date(value: object) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _to_datetime(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def summarize_case(invoice: dict[str, Any]) -> CaseSummary:
    """將一列 invoices 轉為採購列表的一列。

    資料表欄位名稱只在此函式出現，資料表結構變更時只需修改這裡。

    Args:
        invoice: Schema v1.5 invoices 欄位。

    Returns:
        列表資料。
    """
    status = str(invoice["status"])
    return CaseSummary(
        invoice_id=str(invoice["invoice_id"]),
        invoice_no=f"{invoice.get('invoice_track') or ''}{invoice.get('invoice_number') or ''}",
        invoice_date=_to_date(invoice.get("invoice_date")),
        seller_name=invoice.get("seller_name"),
        total_amount=_to_decimal(invoice.get("total_amount")),
        status=status,
        status_label=STATUS_LABELS.get(status, status),
        is_editable=can_edit(status),
        submission_round=int(invoice.get("submission_round") or 0),
        submitted_at=_to_datetime(invoice.get("submitted_at")),
    )


def _matches(summary: CaseSummary, invoice: dict[str, Any], case_filter: CaseFilter) -> bool:
    if case_filter.statuses is not None and summary.status not in case_filter.statuses:
        return False
    if case_filter.date_from and (
        summary.invoice_date is None or summary.invoice_date < case_filter.date_from
    ):
        return False
    if case_filter.date_to and (
        summary.invoice_date is None or summary.invoice_date > case_filter.date_to
    ):
        return False
    keyword = (case_filter.keyword or "").strip().upper()
    if keyword:
        haystack = " ".join(
            str(value or "")
            for value in (
                summary.invoice_no,
                invoice.get("seller_name"),
                invoice.get("seller_tax_id"),
            )
        ).upper()
        return keyword in haystack
    return True


def list_cases(
    invoices: list[dict[str, Any]], scope: CaseScope, case_filter: CaseFilter | None = None
) -> list[CaseSummary]:
    """列出目前選取部門的案件，新送出的在前。

    Args:
        invoices: invoices 資料列。
        scope: 目前使用者與選取部門；只列 department_id 相同的案件。
        case_filter: 篩選條件。

    Returns:
        列表資料；待補正的案件排在最前面，其餘依送出時間由新到舊。
    """
    case_filter = case_filter or CaseFilter()
    matched = []
    for invoice in invoices:
        if str(invoice.get("department_id")) != scope.department_id:
            continue
        summary = summarize_case(invoice)
        if _matches(summary, invoice, case_filter):
            matched.append(summary)
    matched.sort(
        key=lambda s: s.submitted_at.timestamp() if s.submitted_at else float("-inf"),
        reverse=True,
    )
    matched.sort(key=lambda s: s.status != STATUS_RETURNED)
    return matched


# ---- 退回補正 ----


def _parse_return_items(reason: str | None) -> list[ReturnedItem]:
    items = []
    for line in (reason or "").splitlines():
        line = line.strip()
        if not line:
            continue
        field, sep, text = line.partition("：")
        items.append(
            ReturnedItem(field.strip(), text.strip())
            if sep
            else ReturnedItem(UNSPECIFIED_FIELD, line)
        )
    return items


def latest_return(
    invoice_id: str, history: list[dict[str, Any]], return_fields: list[dict[str, Any]]
) -> ReturnRequest | None:
    """取出最近一次退回補正的項目。

    沿用 Schema v1.5 的退回機制：欄位類項目來自 v_invoice_return_fields
    （本輪未確認且財務寫了 review_comment 的欄位）；未修改過的欄位、附件等其他項目
    來自該次 invoice_status_history.reason 的「欄位：原因」逐行文字。

    Args:
        invoice_id: 請款單 UUID。
        history: 該單的 invoice_status_history。
        return_fields: 該單的 v_invoice_return_fields；未寫 review_comment 的列
            （財務未確認但沒指定要補正）不列為退回項目。

    Returns:
        最近一次退回；從未退回時為 None。
    """
    returns = [row for row in history if row.get("new_status") == STATUS_RETURNED]
    if not returns:
        return None
    row = max(returns, key=lambda r: str(r.get("changed_at") or ""))
    field_items = [
        ReturnedItem(
            field=str(field["field_name"]),
            reason=str(field["review_comment"]).strip(),
            item_id=None if field.get("item_id") is None else str(field["item_id"]),
        )
        for field in return_fields
        if (field.get("review_comment") or "").strip()
    ]
    return ReturnRequest(
        invoice_id=invoice_id,
        items=(*field_items, *_parse_return_items(row.get("reason"))),
        returned_by=None if row.get("changed_by") is None else str(row["changed_by"]),
        returned_at=row.get("changed_at"),
    )


def prepare_resubmit(
    invoice: dict[str, Any],
    scope: CaseScope,
    return_request: ReturnRequest,
    responses: list[CorrectionResponse],
    changed_fields: set[tuple[str, str | None]],
) -> ResubmitPlan:
    """重送前逐項檢查回覆，通過後產生交給 C 寫入的重送計畫。

    每個退回項目都要回覆：已補正的欄位必須真的有修改（避免只回覆「已修改」），
    申覆不修改時必須說明理由。

    Args:
        invoice: Schema v1.5 invoices 欄位。
        scope: 目前使用者與選取部門。
        return_request: 最近一次退回的項目。
        responses: 逐項回覆。
        changed_fields: 本次補正實際修改的 (欄位名稱, item_id)；表頭 item_id 為 None，
            更換附件時放入 (attachment, None)。

    Returns:
        重送計畫。

    Raises:
        ProcurementCaseError: 不在待補正、不在部門範圍，或回覆不完整。
    """
    status = str(invoice["status"])
    if status != STATUS_RETURNED:
        raise ProcurementCaseError([ResubmitBlocker("NOT_RETURNED", "只有待補正的案件可以重送")])
    if str(invoice.get("department_id")) != scope.department_id:
        raise ProcurementCaseError([ResubmitBlocker("OUT_OF_SCOPE", "這張請款單不屬於目前部門")])

    by_key = {response.key: response for response in responses}
    returned_keys = [item.key for item in return_request.items]
    blockers = []

    missing = [field for field, item_id in returned_keys if (field, item_id) not in by_key]
    if missing:
        blockers.append(
            ResubmitBlocker(
                "ITEM_NOT_ANSWERED", f"尚有退回項目未回覆：{'、'.join(map(field_label, missing))}"
            )
        )
    unknown = [field for field, item_id in by_key if (field, item_id) not in returned_keys]
    if unknown:
        blockers.append(
            ResubmitBlocker(
                "UNKNOWN_ITEM", f"回覆了不在退回清單的項目：{'、'.join(map(field_label, unknown))}"
            )
        )
    for response in responses:
        if response.action not in RESPONSE_ACTIONS:
            blockers.append(
                ResubmitBlocker(
                    "BAD_ACTION", f"{field_label(response.field)} 的處理方式只能是補正或申覆"
                )
            )
        elif response.action == ACTION_CORRECTED and response.key not in changed_fields:
            blockers.append(
                ResubmitBlocker(
                    "ITEM_NOT_CHANGED", f"{field_label(response.field)} 回覆已補正，但內容沒有修改"
                )
            )
        elif response.action == ACTION_APPEALED and not (response.note or "").strip():
            blockers.append(
                ResubmitBlocker(
                    "APPEAL_NOTE_REQUIRED", f"{field_label(response.field)} 申覆時須說明理由"
                )
            )
    if blockers:
        raise ProcurementCaseError(blockers)

    ordered = tuple(by_key[key] for key in returned_keys)
    reason = "\n".join(
        f"{r.field}：{RESPONSE_ACTIONS[r.action]}" + (f"－{r.note.strip()}" if r.note else "")
        for r in ordered
    )
    return ResubmitPlan(
        invoice_id=str(invoice["invoice_id"]),
        expected_status=status,
        next_round=int(invoice.get("submission_round") or 0) + 1,
        target_status=RESUBMIT_STATUS,
        responses=ordered,
        reason=reason,
    )
