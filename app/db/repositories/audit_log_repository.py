"""
模組名稱：app.db.repositories.audit_log_repository
功能說明：audit_logs（操作稽核紀錄）的新增與查詢。
注意事項：
    - audit_logs 為 append-only：只新增，不提供更新與刪除。
"""

# TODO(JerryChien): add(db, *, user_id, action, target_type, target_id, detail, ip_address) -> AuditLog
# TODO(JerryChien): list_page(db, *, action=None, user_id=None, page, page_size) -> tuple[list[AuditLog], int]
