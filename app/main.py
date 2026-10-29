"""
模組名稱：app.main
功能說明：後台網站進入點；建立 FastAPI app，掛載靜態檔、例外處理與頁面路由。
注意事項：
    - 本機啟動：uvicorn app.main:app --reload --port 8002
    - Vercel 會自動偵測本檔的 `app` 物件作為 Function 進入點，設定見 vercel.json。
    - Vercel 會把 app.mount 的 StaticFiles 提升到 CDN；加上全域 middleware 會讓靜態檔改由
      Function 回應（失去 CDN），因此本專案不使用全域 middleware，登入檢查改用相依注入。
    - 本檔只做「組裝」，不放任何業務邏輯。
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import BASE_DIR, settings
from app.core.exceptions import register_exception_handlers
from app.core.logging_config import setup_logging
from app.routers import (
    accounts,
    auth,
    dashboard,
    db_inspection,
    departments,
    dev_accounts,
    dev_files,
    e_admin,
    e_function_settings,
    health,
    invoices,
    knowledge,
    robots,
    system,
    users,
)

settings.require_environment()
setup_logging()

app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    # 網站不對外提供 API 文件
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# ---- 靜態檔（css / js / img / icons / fonts）----
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")

# ---- 例外處理 ----
register_exception_handlers(app)
app.add_exception_handler(e_admin.EError, e_admin.handle_e_error)

# ---- 頁面路由 ----
app.include_router(health.router)
app.include_router(robots.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(invoices.router)
app.include_router(knowledge.router)
app.include_router(users.router)
app.include_router(accounts.router)
app.include_router(system.router)
app.include_router(db_inspection.router)
app.include_router(departments.router)
# 開發示範：本機頁面與雲端帳號 API（各自只在 remote／cloud 模式提供）
app.include_router(dev_accounts.page)
app.include_router(dev_accounts.api)
app.include_router(dev_files.page)

# 06_E 正式功能；共同接點未就緒時拒絕操作。
app.include_router(e_admin.router)
app.include_router(e_function_settings.router)
