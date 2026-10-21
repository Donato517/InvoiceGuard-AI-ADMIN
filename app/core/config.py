"""
模組名稱：app.core.config
功能說明：集中管理環境變數設定（pydantic-settings），全專案只從這裡讀取設定。
注意事項：
    - 禁止在其他模組直接使用 os.environ / os.getenv。
    - 新增設定時，必須同步更新根目錄 .env.example。
    - 本機從 .env 讀取；Vercel 上由 Project Settings → Environment Variables 注入。
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 專案根目錄（InvoiceGuard-AI-ADMIN/）
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """後台設定。欄位名稱即環境變數名稱。"""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 基本 ----
    APP_NAME: str = "InvoiceGuard-AI-ADMIN"
    APP_ENV: str = "local"  # local / preview / production
    DEBUG: bool = False

    # ---- 資料庫（與 InvoiceGuard-AI-API 共用）----
    DATABASE_URL: str = ""

    # ---- 登入狀態 ----
    SESSION_SECRET_KEY: str = ""
    SESSION_MAX_AGE_SECONDS: int = 8 * 60 * 60

    # ---- Vercel 系統變數（由 Vercel 自動注入，不需手動設定）----
    VERCEL: str = ""
    VERCEL_GIT_COMMIT_SHA: str = ""

    @property
    def is_vercel(self) -> bool:
        """是否執行於 Vercel 環境。"""
        return self.VERCEL == "1"

    @property
    def cookie_secure(self) -> bool:
        """cookie 是否只走 HTTPS。本機開發為 http，因此只有 local 環境關閉。"""
        return self.APP_ENV != "local"

    @property
    def static_version(self) -> str:
        """靜態檔版本參數（部署後網址改變，瀏覽器才會重新下載 css / js）。"""
        return self.VERCEL_GIT_COMMIT_SHA[:7] or "dev"

    @property
    def temp_dir(self) -> Path:
        """暫存目錄。Vercel 上只有 /tmp 可寫入；本機使用專案內 tmp/（已列入 .gitignore）。"""
        if self.is_vercel:
            return Path("/tmp/invoiceguard")
        return BASE_DIR / "tmp"

    @property
    def sqlalchemy_database_url(self) -> str:
        """SQLAlchemy 連線字串：將 postgres:// 轉為 psycopg3 driver 格式。"""
        for prefix in ("postgres://", "postgresql://"):
            if self.DATABASE_URL.startswith(prefix):
                return "postgresql+psycopg://" + self.DATABASE_URL.removeprefix(prefix)
        return self.DATABASE_URL


@lru_cache
def get_settings() -> Settings:
    """取得設定單例（只在第一次呼叫時讀取環境變數）。"""
    return Settings()


settings = get_settings()
