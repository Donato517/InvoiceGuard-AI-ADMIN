"""
模組名稱：app.services.user_service
功能說明：使用者管理——列表搜尋、啟用 / 停用帳號。
相依模組：app.db.repositories.user_repository、app.db.repositories.audit_log_repository
注意事項：
    - 不可停用自己的帳號。
    - 啟用 / 停用必須寫入 audit_logs，且與狀態更新在同一個交易內。
"""

# TODO(JerryChien): list_users(db, *, keyword, page) -> Page
# TODO(JerryChien): set_user_active(db, user_id, is_active, *, operator, ip_address) -> User
