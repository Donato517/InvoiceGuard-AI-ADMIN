"""
模組名稱：app.constants
功能說明：全站共用常數——網站名稱、cookie 名稱、側欄選單、狀態顯示文字。
注意事項：
    - 狀態顯示文字的鍵必須涵蓋 app/db/models/ 中對應 StrEnum 的所有值。
"""

from app.core.constants import SITE_NAMES

# 網站名稱集中在三站共用的 app/core/constants.py
SITE_NAME = SITE_NAMES["ADMIN"]

# 存放已簽署登入資料的 cookie（httpOnly）；與前台的 ig_session 分開
SESSION_COOKIE_NAME = "ig_admin_session"

# 側欄選單：(顯示文字, 網址)；新增頁面時同步更新
MENU_ITEMS: list[tuple[str, str]] = [
    ("儀表板", "/dashboard"),
    ("發票審核", "/invoices"),
    ("知識庫管理", "/knowledge"),
    ("使用者管理", "/users"),
    ("排程紀錄", "/system/job-runs"),
    ("稽核紀錄", "/system/audit-logs"),
]

# 發票作業 9 個步驟（對應 HTML 原型 STEPS）
INVOICE_FLOW_STEPS: list[str] = [
    "採購上傳發票",
    "AI OCR 辨識",
    "採購核對修正",
    "系統自動檢核",
    "財務審核",
    "產生申報檔",
    "匯入國稅局軟體",
    "自動產生傳票",
    "匯出結果",
]

# 發票狀態 → 顯示文字（對應 app.db.models.invoice.InvoiceStatus）
INVOICE_STATUS_LABEL: dict[str, str] = {
    "pending": "待分析",
    "analyzing": "分析中",
    "passed": "通過",
    "flagged": "有風險",
    "rejected": "異常",
}

# 排程工作 → 顯示文字（對應 InvoiceGuard-AI-API 的 app/jobs/ 模組名稱）
JOB_NAME_LABEL: dict[str, str] = {
    "sync_einvoice": "同步財政部電子發票",
    "daily_risk_scan": "每日 AI 風險掃描",
}

# 排程觸發方式 → 顯示文字（對應 app.db.models.job_run.JobTrigger）
JOB_TRIGGER_LABEL: dict[str, str] = {
    "cron": "排程",
    "manual": "手動",
}

# 排程狀態 → 顯示文字（對應 app.db.models.job_run.JobStatus）
JOB_STATUS_LABEL: dict[str, str] = {
    "running": "執行中",
    "success": "成功",
    "failed": "失敗",
}
