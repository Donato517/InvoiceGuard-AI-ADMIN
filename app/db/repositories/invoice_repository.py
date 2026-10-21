"""
模組名稱：app.db.repositories.invoice_repository
功能說明：invoices / invoice_items 的資料存取（審核列表、詳情、更新審核結果）。
注意事項：
    - 只負責 ORM 操作，不含商業邏輯，不自行 commit（交易由 service 控制）。
    - 查詢預設排除軟刪除資料（deleted_at IS NULL）。
"""

# TODO(JerryChien): list_page(db, *, status=None, min_risk_score=None, page, page_size) -> tuple[list[Invoice], int]
# TODO(JerryChien): get_by_id(db, invoice_id) -> Invoice | None（一併載入 items）
