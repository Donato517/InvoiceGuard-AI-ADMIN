"""
模組名稱：app.services.invoice_review_service
功能說明：發票人工複核——審核列表、詳情、核准 / 駁回。
相依模組：app.db.repositories.invoice_repository、app.db.repositories.audit_log_repository
注意事項：
    - 只有 status=flagged 的發票可以複核，結果為 passed 或 rejected。
    - 複核結果與操作者必須寫入 audit_logs，且與狀態更新在同一個交易內。
"""

# TODO(JerryChien): list_invoices(db, *, status, page) -> Page
# TODO(JerryChien): get_invoice(db, invoice_id) -> Invoice（不存在時拋出 NotFoundError）
# TODO(JerryChien): review_invoice(db, invoice_id, *, decision, comment, operator, ip_address) -> Invoice
