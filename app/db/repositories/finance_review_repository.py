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

import json
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
INSERT_RETURN_ITEM = text("""
    INSERT INTO public.invoice_return_items
        (invoice_id, submission_round, item_kind, field_scope, item_id, field_name, reason,
         allowed_fields, created_by)
    VALUES (CAST(:invoice_id AS uuid), :submission_round, :item_kind, :field_scope,
            CAST(:item_id AS uuid), :field_name, :reason, CAST(:allowed_fields AS jsonb),
            CAST(:created_by AS uuid))
""")
LIST_CARRYOVERS = text("""
    SELECT c.submission_round, c.field_scope, c.item_id::text AS item_id, c.field_name,
        r.submission_round AS source_round, r.checked_by::text AS checked_by, r.checked_at
    FROM public.invoice_field_review_carryovers c
    JOIN public.invoice_field_reviews r ON r.field_review_id = c.source_field_review_id
    WHERE c.invoice_id = CAST(:invoice_id AS uuid)
    ORDER BY c.submission_round, c.field_scope, c.field_name
""")
LIST_RETURN_ITEMS = text("""
    SELECT submission_round, item_kind, field_name, reason, status, response_action, response_note,
        responded_at, response_round
    FROM public.invoice_return_items
    WHERE invoice_id = CAST(:invoice_id AS uuid)
    ORDER BY submission_round, created_at, return_item_id
""")
LIST_SNAPSHOTS = text("""
    SELECT submission_round, submission_kind, header, items, submitted_at
    FROM public.invoice_submission_snapshots
    WHERE invoice_id = CAST(:invoice_id AS uuid)
    ORDER BY submission_round
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


TABLE_EXISTS = text("SELECT to_regclass(:name) IS NOT NULL")


def _optional_rows(
    db: Session, table: str, statement: Any, invoice_id: str
) -> list[dict[str, Any]]:
    """讀取重送新增資料表的列；資料表尚未建立（API 的 Migration 還沒套用）時回空清單。

    先以 to_regclass 確認資料表存在再查詢，不開啟 savepoint 或交易、也不吞掉查詢錯誤：
    審核頁因此在 Migration 之前仍可檢視與核准（只是沒有重送資訊），其他資料庫錯誤照常拋出。
    """
    if not db.execute(TABLE_EXISTS, {"name": f"public.{table}"}).scalar_one():
        return []
    return [dict(r) for r in db.execute(statement, {"invoice_id": invoice_id}).mappings()]


def list_carryovers(db: Session, invoice_id: str) -> list[dict[str, Any]]:
    """讀取承接前輪確認效力的欄位（含原確認者、確認時間與來源輪次）；只讀，不推定任何欄位已通過。"""
    return _optional_rows(db, "invoice_field_review_carryovers", LIST_CARRYOVERS, invoice_id)


def list_return_items(db: Session, invoice_id: str) -> list[dict[str, Any]]:
    """讀取各輪的結構化退件項目與申請人回覆（原因與回覆分開保存，只讀）。"""
    return _optional_rows(db, "invoice_return_items", LIST_RETURN_ITEMS, invoice_id)


def list_snapshots(db: Session, invoice_id: str) -> list[dict[str, Any]]:
    """讀取各輪正式提交的完整快照（表頭與明細），供畫面比對修正前後；只讀。"""
    return _optional_rows(db, "invoice_submission_snapshots", LIST_SNAPSHOTS, invoice_id)


def return_item_kind(field: str) -> str:
    """退回項目類型：附件／圖片問題為 image，「其他」為 other，其餘為欄位。"""
    return {"attachment": "image", "其他": "other"}.get(field, "field")


def insert_return_items(db: Session, change: StatusChange, *, submission_round: int) -> None:
    """退回時把每個項目結構化寫入 invoice_return_items（與狀態、歷程同一交易，不 commit）。"""
    for item in change.return_items:
        kind = return_item_kind(item.field)
        is_field = kind == "field"
        db.execute(
            INSERT_RETURN_ITEM,
            {
                "invoice_id": change.invoice_id,
                "submission_round": submission_round,
                "item_kind": kind,
                "field_scope": ("item" if item.item_id else "header") if is_field else None,
                "item_id": item.item_id if is_field else None,
                "field_name": item.field if is_field else None,
                "reason": item.reason,
                "allowed_fields": json.dumps(
                    [{"field_scope": "header", "field_name": name} for name in item.allowed]
                    if is_field
                    else [],
                    ensure_ascii=False,
                ),
                "created_by": change.changed_by,
            },
        )


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
    if change.return_items:
        insert_return_items(db, change, submission_round=submission_round)
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
