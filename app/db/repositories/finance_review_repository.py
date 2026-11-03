"""模組名稱：app.db.repositories.finance_review_repository
功能說明：財務審核的資料庫讀寫——讀取待審案件、欄位確認、修改軌跡與審核歷程；以「狀態仍等於 expected_status」為條件
    更新案件狀態，並與 invoice_status_history、invoice_field_reviews 在同一個交易寫入（交易由 service 提交）。
相依模組：app.schemas.finance_review
注意事項：
    - 只執行 SQL，不 commit、不 rollback，也不做商業判斷（核准條件、狀態轉換表在 finance_review_service）。
    - 表結構以 API 的 Alembic 為準；invoices 目前沒有 deduction_code、account_code 欄位，這兩項不在此寫入
      （service 會把財務填的會計科目與扣抵代號寫進 invoice_status_history.reason 作為紀錄）。
    - 所有 UUID 以 CAST(:x AS uuid) 帶入；呼叫端須先確認字串是合法 UUID。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError  # noqa: F401 - 供 service 捕捉資料庫錯誤（service 不直接 import sqlalchemy.exc）
from sqlalchemy.orm import Session

from app.schemas.finance_review import StatusChange

GET_REVIEW_ROW = text("""
    SELECT i.invoice_id::text AS invoice_id, i.status, i.submission_round,
        i.submitted_by::text AS submitted_by, i.invoice_track, i.invoice_number,
        i.seller_tax_id, i.sales_amount, i.tax_amount, i.total_amount, i.tax_type,
        i.validation_results, i.department_id::text AS department_id
    FROM public.invoices i
    WHERE i.invoice_id = CAST(:invoice_id AS uuid) AND i.status <> 'draft'
""")
LIST_REVIEW_FIELD_REVIEWS = text("""
    SELECT r.submission_round, r.field_scope, r.item_id::text AS item_id, r.field_name,
        r.ai_original_value, r.submitted_value, r.is_checked, r.checked_by::text AS checked_by,
        r.checked_at, r.review_comment
    FROM public.invoice_field_reviews r
    WHERE r.invoice_id = CAST(:invoice_id AS uuid)
    ORDER BY r.submission_round, r.field_scope, r.field_name
""")
LIST_REVIEW_CHANGES = text("""
    SELECT c.submission_round, c.item_id::text AS item_id, c.field_name, c.old_value, c.new_value,
        c.change_source
    FROM public.invoice_changes c
    WHERE c.invoice_id = CAST(:invoice_id AS uuid)
    ORDER BY c.changed_at, c.change_id
""")
LIST_REVIEW_HISTORY = text("""
    SELECT h.old_status, h.new_status, h.reason, h.changed_by::text AS changed_by, h.changed_at
    FROM public.invoice_status_history h
    WHERE h.invoice_id = CAST(:invoice_id AS uuid)
    ORDER BY h.changed_at, h.history_id
""")
UPDATE_STATUS = text("""
    UPDATE public.invoices
    SET status = :new_status,
        reviewed_by = CASE WHEN :record_reviewer THEN CAST(:reviewer AS uuid) ELSE reviewed_by END,
        reviewed_at = CASE WHEN :record_reviewer THEN :changed_at ELSE reviewed_at END,
        tax_type = COALESCE(:tax_type, tax_type),
        updated_at = now()
    WHERE invoice_id = CAST(:invoice_id AS uuid) AND status = :expected_status
    RETURNING invoice_id
""")
UPDATE_TAX_TYPE = text("""
    UPDATE public.invoices
    SET tax_type = :tax_type, updated_at = now()
    WHERE invoice_id = CAST(:invoice_id AS uuid) AND status = :expected_status
    RETURNING invoice_id
""")
INSERT_HISTORY = text("""
    INSERT INTO public.invoice_status_history
        (invoice_id, old_status, new_status, reason, changed_by, changed_at)
    VALUES (CAST(:invoice_id AS uuid), :old_status, :new_status, :reason,
            CAST(:changed_by AS uuid), :changed_at)
""")
UPDATE_FIELD_REVIEW = text("""
    UPDATE public.invoice_field_reviews
    SET is_checked = false, checked_by = NULL, checked_at = NULL, review_comment = :comment,
        updated_at = now()
    WHERE invoice_id = CAST(:invoice_id AS uuid)
      AND submission_round = :submission_round
      AND field_scope = :field_scope
      AND field_name = :field_name
      AND item_id IS NOT DISTINCT FROM CAST(:item_id AS uuid)
""")


def get_review_row(db: Session, invoice_id: str) -> dict[str, Any] | None:
    """讀取審核用的發票表頭；不存在或仍是草稿回 None。"""
    row = db.execute(GET_REVIEW_ROW, {"invoice_id": invoice_id}).mappings().first()
    return dict(row) if row else None


def list_field_reviews(db: Session, invoice_id: str) -> list[dict[str, Any]]:
    """讀取該單所有輪次的欄位確認列（輪次篩選由 build_review_case 處理）。"""
    return [
        dict(r)
        for r in db.execute(LIST_REVIEW_FIELD_REVIEWS, {"invoice_id": invoice_id}).mappings()
    ]


def list_changes(db: Session, invoice_id: str) -> list[dict[str, Any]]:
    """讀取修改軌跡，依修改時間由舊到新。"""
    return [dict(r) for r in db.execute(LIST_REVIEW_CHANGES, {"invoice_id": invoice_id}).mappings()]


def list_history(db: Session, invoice_id: str) -> list[dict[str, Any]]:
    """讀取審核歷程，依時間由舊到新。"""
    return [dict(r) for r in db.execute(LIST_REVIEW_HISTORY, {"invoice_id": invoice_id}).mappings()]


def update_tax_type(db: Session, invoice_id: str, tax_type: str, expected_status: str) -> bool:
    """財務更正課稅別；狀態已被別人改變時回 False（沒有更新任何列）。"""
    row = db.execute(
        UPDATE_TAX_TYPE,
        {"invoice_id": invoice_id, "tax_type": tax_type, "expected_status": expected_status},
    ).first()
    return row is not None


def apply_status_change(
    db: Session,
    change: StatusChange,
    *,
    tax_type: str | None,
    submission_round: int,
    record_reviewer: bool = True,
) -> bool:
    """依 StatusChange 更新案件狀態並寫入審核歷程與退回項目；不 commit。

    只有資料庫狀態仍等於 change.expected_status 才更新（避免兩位財務同時處理同一張單）；
    沒有更新到任何列時回 False，且不寫歷程或欄位確認。

    Args:
        db: 呼叫端控制交易的 Session。
        change: 狀態異動；reviewer 與時間取自其 changed_by／changed_at。
        tax_type: 要一併寫入的課稅別；None 保持不變。
        submission_round: 案件目前輪次，用來定位 invoice_field_reviews。
        record_reviewer: 是否把 change.changed_by 記為審核者（invoices.reviewed_by／reviewed_at）；
            申請人的終止不是財務審核，傳 False 讓審核者欄位維持原值。
    """
    params = {
        "invoice_id": change.invoice_id,
        "new_status": change.new_status,
        "expected_status": change.expected_status,
        "reviewer": change.changed_by,
        "changed_at": change.changed_at,
        "tax_type": tax_type,
        "record_reviewer": record_reviewer,
    }
    if db.execute(UPDATE_STATUS, params).first() is None:
        return False
    db.execute(
        INSERT_HISTORY,
        {
            "invoice_id": change.invoice_id,
            "old_status": change.old_status,
            "new_status": change.new_status,
            "reason": change.reason,
            "changed_by": change.changed_by,
            "changed_at": change.changed_at,
        },
    )
    for review in change.field_review_updates:
        db.execute(
            UPDATE_FIELD_REVIEW,
            {
                "invoice_id": change.invoice_id,
                "submission_round": submission_round,
                "field_scope": review.field_scope,
                "field_name": review.field_name,
                "item_id": review.item_id,
                "comment": review.review_comment,
            },
        )
    return True
