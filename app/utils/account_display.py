"""模組名稱：app.utils.account_display
功能說明：上傳者帳號的顯示與本人判斷（無狀態工具）。
相依模組：無
注意事項：
    - 只改顯示文字，不改資料庫原始值。
    - 「是否本人上傳」只比對真實 User ID，不可用顯示文字判斷。
"""

ACCOUNT_NOT_PROVIDED = "未提供"


def account_display(account: object) -> str:
    """把登入帳號轉成清單顯示用文字。

    Args:
        account: user_accounts.email 原始值；可能為 None 或空白。

    Returns:
        Email 只取 @ 前面；沒有 @ 則顯示完整帳號；空值回「未提供」。
    """
    text = str(account or "").strip()
    if not text:
        return ACCOUNT_NOT_PROVIDED
    local, at, _domain = text.partition("@")
    return local if at and local else text


def is_same_user(uploader_id: object, current_user_id: object) -> bool:
    """比對上傳者與目前登入者的真實 User ID（不分大小寫、去空白）。

    Args:
        uploader_id: invoices.submitted_by。
        current_user_id: 目前登入者的 user_id。

    Returns:
        兩者皆非空且相同才為 True。
    """
    left = str(uploader_id or "").strip().lower()
    right = str(current_user_id or "").strip().lower()
    return bool(left) and left == right
