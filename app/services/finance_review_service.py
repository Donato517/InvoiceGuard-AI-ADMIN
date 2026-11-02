"""
模組名稱：app.services.finance_review_service
功能說明：財務審核（2-4）的業務規則——會計欄位、核准與退回補正。
相依模組：app.schemas.finance_review、app.schemas.account_codes
注意事項：
    - 只留財務一層審核：待財務審核 → 已核准／退回補正；退回後由申請人重送回待財務審核，
      或由申請人終止（procurement_case_service，2026-11-02 起取代財務駁回）。
    - 2026-11-02 使用者決定：核准前不再逐欄確認「AI 原值 → 送出值」，修改軌跡改在「修改歷程」彈窗查看。
      「待財務審核」包含 C 送出時寫入的 pending_department_approval 與 pending_accounting_review。
    - 本模組不存取資料庫；資料表欄位對應集中在 build_review_case()。
      回傳的 StatusChange 須由 repository 以「狀態仍等於 expected_status」為條件更新，
      並與 invoice_status_history 在同一交易寫入，避免兩位財務同時處理同一張單。
    - C 送出時的檢核提醒全部視為 warning，只在審核畫面提示，不需確認也不擋核准
      （2026-11-02 使用者決定取消「確認提醒」）。
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.schemas.account_codes import ACCOUNT_CODES, EXPENSE_CATEGORIES
from app.schemas.finance_review import (
    DEDUCTION_CODES,
    FIELD_LABELS,
    REVIEWABLE_STATUSES,
    STATUS_APPROVED,
    STATUS_RETURNED,
    STATUS_VOIDED,
    TAX_TYPES,
    ChangeTrail,
    FieldReview,
    ReturnItem,
    ReviewBlocker,
    ReviewCase,
    ReviewWarning,
    StatusChange,
)

# 允許的狀態轉換；表上沒有的一律拒絕。退回後重送或終止由申請端執行。
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    **{status: frozenset({STATUS_APPROVED, STATUS_RETURNED}) for status in REVIEWABLE_STATUSES},
    STATUS_RETURNED: REVIEWABLE_STATUSES | {STATUS_VOIDED},
}


class FinanceReviewError(Exception):
    """審核動作不合規則；blockers 列出所有原因，供畫面逐項顯示。"""

    def __init__(self, blockers: list[ReviewBlocker]) -> None:
        super().__init__("；".join(blocker.message for blocker in blockers))
        self.blockers = blockers


# ---- 從資料表組出審核資料 ----


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def warnings_from_validation_results(
    validation_results: dict[str, Any] | None,
) -> list[ReviewWarning]:
    """將 C 寫入 invoices.validation_results 的提醒轉為審核用的 warning。

    Args:
        validation_results: 格式為 {"warnings": [{field, code, message}]}。

    Returns:
        尚未確認的提醒清單；格式不符的項目略過。
    """
    raw = (validation_results or {}).get("warnings") or []
    return [
        ReviewWarning(
            field=str(item["field"]), code=str(item["code"]), message=str(item["message"])
        )
        for item in raw
        if isinstance(item, dict) and {"field", "code", "message"} <= item.keys()
    ]


def build_review_case(invoice: dict[str, Any], field_reviews: list[dict[str, Any]]) -> ReviewCase:
    """將一列 invoices 與其 invoice_field_reviews 組成審核資料。

    資料表欄位名稱只在此函式出現，資料表結構變更時只需修改這裡。

    Args:
        invoice: Schema v1.5 invoices 欄位；deduction_code 尚無欄位，暫由同名鍵讀取。
        field_reviews: 同一張單的 invoice_field_reviews；只取與 invoices.submission_round
            相同輪次的列，退回前的舊輪不再需要確認。

    Returns:
        審核資料。
    """
    # TODO(StacieChen): v1.5 尚無 deduction_code 欄位與 warning 確認紀錄，Schema 補上後改讀資料表
    submission_round = int(invoice.get("submission_round") or 0)
    return ReviewCase(
        invoice_id=str(invoice["invoice_id"]),
        status=str(invoice["status"]),
        submission_round=submission_round,
        submitted_by=_optional_str(invoice.get("submitted_by")),
        invoice_track=_optional_str(invoice.get("invoice_track")),
        invoice_number=_optional_str(invoice.get("invoice_number")),
        seller_tax_id=_optional_str(invoice.get("seller_tax_id")),
        sales_amount=_to_decimal(invoice.get("sales_amount")),
        tax_amount=_to_decimal(invoice.get("tax_amount")),
        total_amount=_to_decimal(invoice.get("total_amount")),
        tax_type=_optional_str(invoice.get("tax_type")),
        deduction_code=_optional_str(invoice.get("deduction_code")),
        field_reviews=tuple(
            FieldReview(
                field_scope=str(row["field_scope"]),
                field_name=str(row["field_name"]),
                item_id=_optional_str(row.get("item_id")),
                ai_original_value=row.get("ai_original_value"),
                submitted_value=row.get("submitted_value"),
                is_checked=bool(row.get("is_checked")),
                checked_by=_optional_str(row.get("checked_by")),
                checked_at=row.get("checked_at"),
                review_comment=row.get("review_comment"),
            )
            for row in field_reviews
            if int(row.get("submission_round") or 0) == submission_round
        ),
        warnings=tuple(warnings_from_validation_results(invoice.get("validation_results"))),
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def account_category(account_code: str) -> str | None:
    """會計科目所屬的費用大項；畫面依此預選費用大項。

    Args:
        account_code: 會計科目代碼，例如 6211004；可帶名稱（6211004 管理-文具用品），只取第一段。

    Returns:
        費用大項；不在科目清單時為 None。
    """
    code = account_code.strip().split(" ", 1)[0]
    return ACCOUNT_CODES[code][1] if code in ACCOUNT_CODES else None


def check_account_code(category: str, account_code: str) -> None:
    """檢查費用大項與會計科目：要先選費用大項，會計科目須屬於該大項。

    Args:
        category: 費用大項；空白代表未選。
        account_code: 會計科目代碼；空白代表未選，不檢查。

    Raises:
        FinanceReviewError: 大項不在清單、選了科目卻沒選大項、科目不在清單或不屬於該大項。
    """
    if category and category not in EXPENSE_CATEGORIES:
        raise FinanceReviewError([ReviewBlocker("BAD_EXPENSE_CATEGORY", "費用大項不在清單中")])
    if not account_code:
        return
    if not category:
        raise FinanceReviewError(
            [ReviewBlocker("EXPENSE_CATEGORY_MISSING", "請先選擇費用大項，再選會計科目")]
        )
    actual = account_category(account_code)
    if actual is None:
        raise FinanceReviewError([ReviewBlocker("BAD_ACCOUNT_CODE", "會計科目不在科目清單中")])
    if actual != category:
        raise FinanceReviewError(
            [
                ReviewBlocker(
                    "ACCOUNT_CATEGORY_MISMATCH",
                    f"會計科目屬於「{actual}」，與所選費用大項「{category}」不同",
                )
            ]
        )


def field_label(field_name: str) -> str:
    """欄位的中文名稱，供審核畫面顯示。

    Args:
        field_name: 欄位名稱；刪除的 AI 明細為 deleted_item_{AI 明細序號}。

    Returns:
        中文名稱；未知欄位回傳原名稱。
    """
    if field_name.startswith("deleted_item_"):
        return f"刪除的 AI 明細第 {field_name.removeprefix('deleted_item_')} 筆"
    return FIELD_LABELS.get(field_name, field_name)


def build_change_history(changes: list[dict[str, Any]]) -> list[ChangeTrail]:
    """將 invoice_changes 依欄位整理成修改軌跡，供審核畫面顯示（唯讀，不影響核准）。

    Args:
        changes: 同一張單的 invoice_changes，需已依 changed_at 由舊到新排序。

    Returns:
        每個欄位一筆，依該欄位第一次修改的先後排列；
        values 為原值加上每次修改後的值，例如 6030 → 6330 → 6300。
    """
    trails: dict[tuple[int, str | None, str], list[dict[str, Any]]] = {}
    for row in changes:
        key = (
            int(row.get("submission_round") or 0),
            _optional_str(row.get("item_id")),
            str(row["field_name"]),
        )
        trails.setdefault(key, []).append(row)
    return [
        ChangeTrail(
            submission_round=submission_round,
            item_id=item_id,
            field_name=field_name,
            values=(rows[0].get("old_value"), *(row.get("new_value") for row in rows)),
            change_sources=tuple(str(row.get("change_source") or "user") for row in rows),
        )
        for (submission_round, item_id, field_name), rows in trails.items()
    ]


# ---- 審核中的修改 ----


def _require_reviewable(case: ReviewCase) -> None:
    if case.status not in REVIEWABLE_STATUSES:
        raise FinanceReviewError(
            [ReviewBlocker("NOT_REVIEWABLE", "這張請款單目前不在待財務審核狀態")]
        )


def update_accounting_fields(
    case: ReviewCase, tax_type: str | None, deduction_code: str | None
) -> ReviewCase:
    """更新財務審核時填寫的課稅別與扣抵代號。

    Args:
        case: 審核資料。
        tax_type: taxable／zero_rated／tax_exempt；None 代表不變。
        deduction_code: 1～4；None 代表不變。

    Returns:
        更新後的審核資料。

    Raises:
        FinanceReviewError: 不在審核狀態或代碼不合法。
    """
    _require_reviewable(case)
    blockers = []
    if tax_type is not None and tax_type not in TAX_TYPES:
        blockers.append(ReviewBlocker("BAD_TAX_TYPE", "課稅別只能是應稅、零稅率或免稅"))
    if deduction_code is not None and deduction_code not in DEDUCTION_CODES:
        blockers.append(ReviewBlocker("BAD_DEDUCTION_CODE", "扣抵代號只能是 1～4"))
    if blockers:
        raise FinanceReviewError(blockers)
    return replace(
        case,
        tax_type=case.tax_type if tax_type is None else tax_type,
        deduction_code=case.deduction_code if deduction_code is None else deduction_code,
    )


# ---- 審核決定 ----


def approval_blockers(case: ReviewCase, reviewer: str) -> list[ReviewBlocker]:
    """列出所有不能核准的原因；空清單代表可以核准。

    Args:
        case: 審核資料。
        reviewer: 要核准的財務人員 user_id。

    Returns:
        不能核准的原因。
    """
    if case.status not in REVIEWABLE_STATUSES:
        return [ReviewBlocker("NOT_REVIEWABLE", "這張請款單目前不在待財務審核狀態")]
    blockers = []
    if case.submitted_by is not None and case.submitted_by == reviewer:
        blockers.append(ReviewBlocker("SELF_APPROVAL", "不能核准自己送出的請款單"))
    if case.tax_type not in TAX_TYPES:
        blockers.append(ReviewBlocker("TAX_TYPE_MISSING", "請選擇課稅別"))
    if case.deduction_code not in DEDUCTION_CODES:
        blockers.append(ReviewBlocker("DEDUCTION_CODE_MISSING", "請選擇扣抵代號"))
    return blockers


def _change(
    case: ReviewCase, new_status: str, reviewer: str, now: datetime, **kwargs: Any
) -> StatusChange:
    if new_status not in ALLOWED_TRANSITIONS.get(case.status, frozenset()):
        raise FinanceReviewError(
            [ReviewBlocker("TRANSITION_NOT_ALLOWED", "目前狀態不能執行這個動作")]
        )
    return StatusChange(
        invoice_id=case.invoice_id,
        old_status=case.status,
        new_status=new_status,
        changed_by=reviewer,
        changed_at=now,
        **kwargs,
    )


def approve(case: ReviewCase, reviewer: str, now: datetime) -> StatusChange:
    """核准請款單；核准後才可列入申報檔。

    Args:
        case: 審核資料。
        reviewer: 財務人員 user_id。
        now: 核准時間。

    Returns:
        狀態異動。

    Raises:
        FinanceReviewError: 有任何不能核准的原因。
    """
    if blockers := approval_blockers(case, reviewer):
        raise FinanceReviewError(blockers)
    return _change(case, STATUS_APPROVED, reviewer, now, reason=None)


def return_to_applicant(
    case: ReviewCase, reviewer: str, items: list[ReturnItem], now: datetime
) -> StatusChange:
    """退回申請人補正，須逐項列出要補什麼。

    沿用 Schema v1.5 的退回機制：項目若對應本輪 invoice_field_reviews 的欄位，
    原因寫入該列 review_comment 並改回未確認，申請人由 v_invoice_return_fields 看到；
    沒有對應列的項目（未修改過的欄位、附件）才寫入 invoice_status_history.reason。

    Args:
        case: 審核資料。
        reviewer: 財務人員 user_id。
        items: 退回項目，至少一項且欄位與原因不可空白；明細欄位需帶 item_id。
        now: 退回時間。

    Returns:
        狀態異動；field_review_updates 為要同交易更新的欄位確認列。

    Raises:
        FinanceReviewError: 不在審核狀態、沒有退回項目或原因空白。
    """
    _require_reviewable(case)
    cleaned = tuple(ReturnItem(i.field.strip(), i.reason.strip(), i.item_id) for i in items)
    if not cleaned or any(not i.field or not i.reason for i in cleaned):
        raise FinanceReviewError(
            [ReviewBlocker("RETURN_ITEMS_REQUIRED", "退回時須逐項填寫欄位與補正原因")]
        )
    reviews = {(r.field_name, r.item_id): r for r in case.field_reviews}
    updates = []
    others = []
    for item in cleaned:
        review = reviews.get((item.field, item.item_id))
        if review is None:
            others.append(item)
            continue
        updates.append(
            replace(
                review,
                is_checked=False,
                checked_by=None,
                checked_at=None,
                review_comment=item.reason,
            )
        )
    reason = "\n".join(f"{i.field}：{i.reason}" for i in others) or None
    return _change(
        case,
        STATUS_RETURNED,
        reviewer,
        now,
        reason=reason,
        return_items=cleaned,
        field_review_updates=tuple(updates),
    )
