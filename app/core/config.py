"""
模組名稱：app.core.config
功能說明：集中管理環境變數設定（pydantic-settings），全專案只從這裡讀取設定。
注意事項：
    - 禁止在其他模組直接使用 os.environ / os.getenv。
    - 新增設定時，必須同步更新根目錄 .env.example。
    - 預設不讀取 .env / secrets 目錄；Vercel 由平台注入程序環境變數。
    - 本機預設 remote 模式，不能以本模組建立公司資料庫連線。
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.module_errors import (
    ConfigurationIssue,
    ConfigurationReport,
    ConfigurationScope,
    ModuleConfigurationError,
)

# 專案根目錄（InvoiceGuard-AI-ADMIN/）
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """後台設定。欄位名稱即環境變數名稱。"""

    model_config = SettingsConfigDict(
        env_file=None,
        secrets_dir=None,
        extra="forbid",
        validate_assignment=True,
        hide_input_in_errors=True,
    )

    # ---- 基本 ----
    APP_NAME: str = "InvoiceGuard-AI-ADMIN"
    APP_ENV: Literal["local", "preview", "production"] = "local"
    DEBUG: bool = False
    MODULE_BACKEND: Literal["auto", "remote", "cloud"] = "auto"
    REMOTE_ADMIN_BASE_URL: str = Field(default="", repr=False)

    # ---- 資料庫（與 InvoiceGuard-AI-API 共用）----
    DATABASE_URL: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    DB_POOL_MODE: Literal["transaction", "session", "direct"] = "transaction"
    DB_SSL_MODE: Literal["require", "verify-full"] = "require"
    DB_CONNECT_TIMEOUT_SECONDS: int = Field(default=5, ge=1, le=30)

    # ---- 雲端 Storage 與預留模型設定（本階段不呼叫外部服務）----
    SUPABASE_URL: str = Field(default="", repr=False)
    SUPABASE_STORAGE_BUCKET: str = ""
    SUPABASE_SECRET_KEY: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    GEMINI_API_KEY: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    OPENAI_API_KEY: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)

    # ---- 登入狀態 ----
    SESSION_SECRET_KEY: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
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
        return self.is_vercel or self.APP_ENV != "local"

    @property
    def module_backend(self) -> Literal["remote", "cloud"]:
        """auto 模式只在 Vercel 選擇雲端；此選擇不代表完成授權。"""
        if self.MODULE_BACKEND == "auto":
            return "cloud" if self.is_vercel else "remote"
        return self.MODULE_BACKEND

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
        raw_url = self.DATABASE_URL.get_secret_value()
        for prefix in ("postgres://", "postgresql://"):
            if raw_url.startswith(prefix):
                return "postgresql+psycopg://" + raw_url.removeprefix(prefix)
        return raw_url

    def check_configuration(self, scope: ConfigurationScope) -> ConfigurationReport:
        """驗證指定能力需要的設定；不連網、不建立 Engine、不輸出值。"""
        if scope not in {"local", "database", "storage", "session", "cloud"}:
            raise ValueError("不支援的設定檢查範圍")
        issues: list[ConfigurationIssue] = []
        if scope == "local":
            if self.is_vercel or self.module_backend != "remote":
                issues.append(
                    ConfigurationIssue("MODULE_BACKEND", "remote_required", "本機應使用遠端模式")
                )
            for name in (
                "DATABASE_URL",
                "SESSION_SECRET_KEY",
                "SUPABASE_SECRET_KEY",
                "GEMINI_API_KEY",
                "OPENAI_API_KEY",
            ):
                if getattr(self, name).get_secret_value():
                    issues.append(
                        ConfigurationIssue(name, "cloud_secret_in_local", "本機不應載入公司機密")
                    )
            if not self.REMOTE_ADMIN_BASE_URL:
                issues.append(
                    ConfigurationIssue(
                        "REMOTE_ADMIN_BASE_URL",
                        "remote_not_configured",
                        "尚未設定遠端服務，目前可執行離線契約測試",
                        "warning",
                    )
                )
            elif not _is_https_base_url(self.REMOTE_ADMIN_BASE_URL):
                issues.append(
                    ConfigurationIssue(
                        "REMOTE_ADMIN_BASE_URL",
                        "invalid_https_url",
                        "需為不含帳密、查詢或片段的 HTTPS 基底網址",
                    )
                )
        else:
            if not self.is_vercel or self.module_backend != "cloud":
                issues.append(
                    ConfigurationIssue(
                        "MODULE_BACKEND",
                        "cloud_runtime_required",
                        "目前僅允許在 Vercel 雲端使用直接整合",
                    )
                )
            if self.APP_ENV == "local":
                issues.append(
                    ConfigurationIssue(
                        "APP_ENV", "cloud_environment_required", "雲端需使用 preview 或 production"
                    )
                )
            if self.DEBUG:
                issues.append(ConfigurationIssue("DEBUG", "debug_in_cloud", "雲端需關閉除錯輸出"))
            if scope in {"database", "cloud"}:
                issues.extend(self._database_issues())
            if scope in {"storage", "cloud"}:
                if not _is_https_base_url(self.SUPABASE_URL):
                    issues.append(
                        ConfigurationIssue(
                            "SUPABASE_URL", "invalid_https_url", "需設定 Supabase HTTPS 專案網址"
                        )
                    )
                if not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", self.SUPABASE_STORAGE_BUCKET
                ):
                    issues.append(
                        ConfigurationIssue(
                            "SUPABASE_STORAGE_BUCKET",
                            "invalid_bucket",
                            "需設定單一合法 Bucket 名稱",
                        )
                    )
                if not self.SUPABASE_SECRET_KEY.get_secret_value().strip():
                    issues.append(
                        ConfigurationIssue(
                            "SUPABASE_SECRET_KEY", "required", "雲端 Storage 需要伺服器端憑證"
                        )
                    )
            if scope in {"session", "cloud"}:
                if len(self.SESSION_SECRET_KEY.get_secret_value().strip()) < 32:
                    issues.append(
                        ConfigurationIssue(
                            "SESSION_SECRET_KEY",
                            "secret_too_short",
                            "需使用至少 32 字元的雲端簽署金鑰",
                        )
                    )
                if not 1 <= self.SESSION_MAX_AGE_SECONDS <= 86400:
                    issues.append(
                        ConfigurationIssue(
                            "SESSION_MAX_AGE_SECONDS",
                            "invalid_duration",
                            "有效期間需介於 1 至 86400 秒",
                        )
                    )
        issues.append(
            ConfigurationIssue(
                "modules",
                "contracts_only",
                "目前只完成靜態設定驗證與契約，尚未驗證連線或接線實際服務",
                "warning",
            )
        )
        return ConfigurationReport(scope, tuple(issues))

    def require_configuration(self, scope: ConfigurationScope) -> None:
        """設定不符合能力時阻擋操作，訊息不含設定值。"""
        report = self.check_configuration(scope)
        if not report.is_valid:
            raise ModuleConfigurationError(report)

    def _database_issues(self) -> list[ConfigurationIssue]:
        try:
            url = make_url(self.sqlalchemy_database_url)
            if (
                url.drivername != "postgresql+psycopg"
                or not all((url.host, url.username, url.password, url.database))
                or (url.port is not None and not 1 <= url.port <= 65535)
            ):
                raise ValueError
        except (ArgumentError, ValueError, TypeError):
            return [
                ConfigurationIssue(
                    "DATABASE_URL", "invalid_postgres_url", "需設定完整 PostgreSQL 連線字串"
                )
            ]
        ssl_mode = url.query.get("sslmode")
        if ssl_mode is not None and ssl_mode not in {"require", "verify-ca", "verify-full"}:
            return [
                ConfigurationIssue("DATABASE_URL", "unsafe_ssl_mode", "雲端資料庫連線必須使用 TLS")
            ]
        return []


def _is_https_base_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return bool(
            url.scheme == "https"
            and url.hostname
            and url.port in {None, 443}
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and not any(character.isspace() for character in value)
            and "\\" not in value
        )
    except ValueError:
        return False


@lru_cache
def get_settings() -> Settings:
    """取得設定單例（只在第一次呼叫時讀取環境變數）。"""
    return Settings()


settings = get_settings()
