"""
模組名稱：app.core.config
功能說明：集中管理環境變數設定（pydantic-settings），全專案只從這裡讀取設定。
注意事項：
    - 禁止在其他模組直接使用 os.environ / os.getenv。
    - 新增設定時，必須同步更新根目錄 .env.example。
    - 預設不讀取 .env / secrets 目錄；Vercel 由平台注入程序環境變數。
    - DB 連線不限制執行位置；本機已有合法 DATABASE_URL 時可直接使用。
"""

import re
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal

from psycopg import pq
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.constants import PRODUCTION
from app.core.environment import (
    EnvironmentConfigurationError,
    environment_report,
    settings_sources,
    valid_service_url,
)
from app.core.module_errors import (
    ConfigurationIssue,
    ConfigurationReport,
    ConfigurationScope,
    ModuleConfigurationError,
)

# 專案根目錄（InvoiceGuard-AI-ADMIN/）
BASE_DIR = Path(__file__).resolve().parents[2]

# psycopg 將 URL 查詢參數原樣交給 libpq；不在此清單的參數只會在連線時才失敗
LIBPQ_OPTIONS = frozenset(option.keyword.decode() for option in pq.Conninfo.get_defaults())

# 保留的 remote_admin_client 預設 ADMIN 公開網址（目前無呼叫端）；網址集中在 app/core/constants.py，
# dev-urls.local.json 有指定時以其為準
DEFAULT_REMOTE_ADMIN_BASE_URL = PRODUCTION["ADMIN_URL"]


class Settings(BaseSettings):
    """後台設定。欄位名稱即環境變數名稱。"""

    model_config = SettingsConfigDict(
        env_file=None,
        secrets_dir=None,
        extra="forbid",
        validate_assignment=True,
        hide_input_in_errors=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[Any, ...]:
        """程序環境優先；只有本機可載入核定公開 URL，永不載入 dotenv/secrets。"""
        return settings_sources(init_settings, env_settings, repo_root=BASE_DIR, platform="admin")

    # ---- 基本 ----
    APP_NAME: str = "InvoiceGuard-AI-ADMIN"
    APP_ENV: Literal["local", "preview", "production"] = "local"
    DEBUG: bool = False
    LOCAL_SECRET_FIELDS: tuple[str, ...] = Field(default=(), exclude=True, repr=False)
    MODULE_BACKEND: Literal["auto", "remote", "cloud"] = "auto"
    REMOTE_ADMIN_BASE_URL: str = Field(default="", repr=False)

    # ---- 資料庫（與 InvoiceGuard-AI-API 共用）----
    DATABASE_URL: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    DB_POOL_MODE: Literal["transaction", "session", "direct"] = "transaction"
    DB_SSL_MODE: Literal["require", "verify-full"] = "require"
    DB_CONNECT_TIMEOUT_SECONDS: int = Field(default=5, ge=1, le=30)

    # ---- 第一階段設定檢查、S3 檔案存取與預留模型設定 ----
    SUPABASE_URL: str = Field(default="", repr=False)
    SUPABASE_STORAGE_BUCKET: str = ""
    SUPABASE_SECRET_KEY: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    # Supabase Storage 的 S3 相容端點；本機由 START 經 Vercel CLI 注入（Bucket 沿用 SUPABASE_STORAGE_BUCKET）
    S3_ENDPOINT_URL: str = Field(default="", repr=False)
    S3_REGION: str = ""
    S3_ACCESS_KEY_ID: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    S3_SECRET_ACCESS_KEY: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    OPENAI_API_KEY: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)

    # ---- AI 模組（與 API 同版的 Document AI／Gemini／Groq 模組；2026-10-29 使用者核定 ADMIN 可使用）----
    # 只提供模組與設定，尚未接線任何功能；本機由 START 經 Vercel CLI 注入，雲端由 Vercel ADMIN 專案提供。
    GEMINI_API_KEY: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    # Gemini 對話模型名稱；不是機密，未設定時用預設值（與 API 相同）
    GEMINI_MODEL: str = "gemini-3.8-flash"
    # Groq 對話（gpt-oss）；預設與識別用 20B，RAG／討論與進階備用用 120B（與 API 相同）
    GROQ_API_KEY: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    GROQ_DEFAULT_MODEL: str = "openai/gpt-oss-20b"
    GROQ_ADVANCED_MODEL: str = "openai/gpt-oss-120b"
    # Google Document AI（OCR）
    GOOGLE_CLOUD_PROJECT_ID: str = ""
    GOOGLE_CLOUD_LOCATION: str = ""
    DOCUMENT_AI_PROCESSOR_ID: str = ""
    GOOGLE_SERVICE_ACCOUNT_JSON: SecretStr = Field(
        default_factory=lambda: SecretStr(""), repr=False
    )

    # START 經 Vercel CLI 注入本機 ADMIN 程序的雲端設定：值只在該程序記憶體，不視為本機禁用機密
    # （DATABASE_URL 2026-10-25；AI 模組的 Gemini、Google 服務帳號、Groq 2026-10-29 使用者核定）。
    # 其他公司機密（SESSION_SECRET_KEY、SUPABASE_SECRET_KEY、OPENAI_API_KEY 等）在本機仍拒絕。
    LOCAL_INJECTED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"DATABASE_URL", "GEMINI_API_KEY", "GOOGLE_SERVICE_ACCOUNT_JSON", "GROQ_API_KEY"}
    )

    # ---- 登入狀態 ----
    SESSION_SECRET_KEY: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)
    SESSION_MAX_AGE_SECONDS: int = 8 * 60 * 60

    # ---- Vercel 系統變數（由 Vercel 自動注入，不需手動設定）----
    VERCEL: str = ""
    VERCEL_ENV: str = ""
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
    def remote_admin_base_url(self) -> str:
        """保留的 ADMIN 目標：解析後為 remote 時取指定值或預設，其餘回空字串；目前無呼叫端。"""
        if self.module_backend != "remote":
            return ""
        return self.REMOTE_ADMIN_BASE_URL or DEFAULT_REMOTE_ADMIN_BASE_URL

    @property
    def dev_pages_enabled(self) -> bool:
        """只在 APP_ENV=local、VERCEL 不為 1 且解析後 module_backend=remote 時開放 /dev 示範。"""
        return self.APP_ENV == "local" and not self.is_vercel and self.module_backend == "remote"

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
        issues = [ConfigurationIssue(**issue) for issue in self.check_environment()["issues"]]
        if scope == "database":
            # DB 能力只要求合法連線設定，不要求 Vercel 標記或雲端部署。
            issues.extend(self._database_issues())
        elif scope == "local":
            if self.is_vercel or self.module_backend != "remote":
                issues.append(
                    ConfigurationIssue("MODULE_BACKEND", "remote_required", "本機應使用遠端模式")
                )
            for name in (
                "SESSION_SECRET_KEY",
                "SUPABASE_SECRET_KEY",
                "GEMINI_API_KEY",
                "OPENAI_API_KEY",
            ):
                # START 經 Vercel CLI 注入的核定設定可在本機使用
                if (
                    name not in self.LOCAL_INJECTED_FIELDS
                    and getattr(self, name).get_secret_value()
                ):
                    issues.append(
                        ConfigurationIssue(name, "cloud_secret_in_local", "本機不應載入公司機密")
                    )
            if not self.REMOTE_ADMIN_BASE_URL and not self.DATABASE_URL.get_secret_value():
                issues.append(
                    ConfigurationIssue(
                        "REMOTE_ADMIN_BASE_URL",
                        "remote_not_configured",
                        "尚未設定遠端服務，目前可執行離線契約測試",
                        "warning",
                    )
                )
            elif self.REMOTE_ADMIN_BASE_URL and not _is_https_base_url(self.REMOTE_ADMIN_BASE_URL):
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
        unique_issues: dict[tuple[str, str], ConfigurationIssue] = {}
        for issue in issues:
            unique_issues.setdefault((issue.field, issue.code), issue)
        return ConfigurationReport(scope, tuple(unique_issues.values()))

    def check_environment(self) -> dict[str, Any]:
        """驗證本站邊界；START 注入的核定雲端設定（LOCAL_INJECTED_FIELDS）可在本機使用，其他機密規則不變。"""
        report = environment_report(self, "admin")
        report["issues"] = [
            issue
            for issue in report["issues"]
            if not (
                issue["field"] in self.LOCAL_INJECTED_FIELDS
                and issue["code"] == "cloud_secret_in_local"
            )
        ]
        if (
            self.APP_ENV == "local"
            and not self.VERCEL
            and not self.VERCEL_ENV
            and self.DATABASE_URL.get_secret_value()
        ):
            report["issues"].extend(asdict(issue) for issue in self._database_issues())
        report["configuration_valid"] = not any(
            issue["severity"] == "error" for issue in report["issues"]
        )
        return report

    def require_environment(self) -> None:
        """使用本站環境報告，允許已核定的本機 DB 設定並保留其他啟動檢查。"""
        report = self.check_environment()
        if not report["configuration_valid"]:
            codes = ", ".join(
                f"{issue['field']}:{issue['code']}"
                for issue in report["issues"]
                if issue["severity"] == "error"
            )
            raise EnvironmentConfigurationError("環境設定驗證失敗：" + codes)

    def require_configuration(self, scope: ConfigurationScope) -> None:
        """設定不符合能力時阻擋操作，訊息不含設定值。"""
        report = self.check_configuration(scope)
        if not report.is_valid:
            raise ModuleConfigurationError(report)

    def _database_issues(self) -> list[ConfigurationIssue]:
        if not self.DATABASE_URL.get_secret_value().strip():
            return [ConfigurationIssue("DATABASE_URL", "required", "需設定 DATABASE_URL")]
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
        issues: list[ConfigurationIssue] = []
        ssl_mode = url.query.get("sslmode")
        if ssl_mode is not None and ssl_mode not in {"require", "verify-ca", "verify-full"}:
            issues.append(
                ConfigurationIssue("DATABASE_URL", "unsafe_ssl_mode", "雲端資料庫連線必須使用 TLS")
            )
        # 例如 Supabase 整合的 supa、Prisma 的 pgbouncer；不提早擋下會被誤判成 DB 連線失敗
        if not set(url.query) <= LIBPQ_OPTIONS:
            issues.append(
                ConfigurationIssue(
                    "DATABASE_URL",
                    "unsupported_url_option",
                    "連線字串含 PostgreSQL 驅動不支援的參數（例如 supa、pgbouncer），需移除",
                )
            )
        return issues


def _is_https_base_url(value: str) -> bool:
    return valid_service_url(value)


@lru_cache
def get_settings() -> Settings:
    """取得設定單例（只在第一次呼叫時讀取環境變數）。"""
    return Settings()


settings = get_settings()
