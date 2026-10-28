"""模組名稱：app.services.admin_login_service；功能說明：帳密驗證、網站 Session 與部門四操作。

網站 Session 採本站已使用的 itsdangerous 簽章 Cookie；DB 登入只更新 last_login_at。
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

from itsdangerous import BadData, URLSafeTimedSerializer
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.db.repositories import admin_login_repository as repository
from app.db.repositories.e_repository import open_session
from app.schemas.e_access import AccessSnapshot, EffectiveDepartment, SidebarEntry
from app.schemas.e_admin import (
    Action,
    DataScope,
    Decision,
    DeniedError,
    EError,
    Identity,
    IssuedSession,
    Profile,
    ProfilePatch,
    UnauthenticatedError,
    UnavailableError,
)
from app.services.e_registry import Capability

CATALOG_CODES = {
    "sys.accounts": "accounts",
    "sys.departments": "department",
    "sys.features": "functions",
    "sys.department-permissions": "department",
}
ACTION_FLAGS = {Action.VIEW: 1, Action.CREATE: 2, Action.EDIT: 4, Action.DELETE: 8}


@lru_cache(maxsize=1)
def _local_session_key() -> str:
    # 開發用暫存簽章金鑰只在本站程序記憶體，重啟後重登；不借用 DB／API 密鑰。
    return secrets.token_urlsafe(48)


def _serializer() -> URLSafeTimedSerializer:
    if settings.APP_ENV == "local" and not settings.is_vercel:
        key = _local_session_key()
    else:
        settings.require_configuration("session")
        key = settings.SESSION_SECRET_KEY.get_secret_value()
    return URLSafeTimedSerializer(key, salt="invoiceguard-admin-department-session-v1")


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(32))


def _verify(password: SecretStr, encoded: str) -> bool:
    try:
        return verify_password(password.get_secret_value(), encoded)
    except Exception:
        return False


def _valid_account(row: dict[str, Any] | None) -> bool:
    return bool(
        row
        and row["account_status"] == "active"
        and row["profile_active"]
        and row["resigned_at"] is None
        and row["credential_version"] >= 1
        and (row["locked_until"] is None or row["locked_until"] <= datetime.now(UTC))
    )


class DepartmentLoginAdapter:
    """讀既有帳號／部門勾選，組成 Session；不建立管理員、角色、授權或 DB Session。"""

    def __init__(
        self,
        registry: tuple[Capability, ...],
        session_factory: Callable[[], Session] = open_session,
    ) -> None:
        self.registry = registry
        self.session_factory = session_factory

    def _run[T](self, operation: Callable[[Session], T], *, write: bool = False) -> T:
        db = self.session_factory()
        try:
            result = operation(db)
            if write:
                db.commit()
            return result
        except EError:
            raise
        except Exception:
            raise UnavailableError from None
        finally:
            try:
                try:
                    db.rollback()
                finally:
                    db.close()
            except Exception:
                raise UnavailableError from None

    @staticmethod
    def _flags(rows: tuple[dict[str, Any], ...]) -> dict[str, int]:
        result = {}
        for row in rows:
            if row["is_active"] and row["app_scope"] in {"admin", "both"}:
                result[row["function_code"]] = sum(
                    flag for action, flag in ACTION_FLAGS.items() if row["can_" + action.value]
                )
        return result

    def login(self, email: str, password: SecretStr) -> IssuedSession:
        """驗證成功後讀目前部門功能、組合網站 Session，唯一 DB 寫入是登入時間。"""
        if not 1 <= len(password.get_secret_value()) <= 1024:
            raise UnauthenticatedError

        def operation(db: Session) -> IssuedSession:
            repository.prepare(db)
            row = repository.login_account(db, email.strip().lower())
            matched = _verify(password, row["password_hash"] if row else _dummy_hash())
            if not matched or not _valid_account(row):
                raise UnauthenticatedError
            departments = repository.departments(db, row["user_id"])
            if row["primary_department_id"] not in {item["department_id"] for item in departments}:
                raise UnauthenticatedError
            flags = self._flags(
                repository.functions(db, row["user_id"], row["primary_department_id"])
            )
            payload = {
                "v": 1,
                "uid": str(row["user_id"]),
                "sid": str(uuid4()),
                "department": str(row["primary_department_id"]),
                "cv": row["credential_version"],
                "expires": int(
                    (
                        datetime.now(UTC) + timedelta(seconds=settings.SESSION_MAX_AGE_SECONDS)
                    ).timestamp()
                ),
                "functions": {code: flags for code, flags in flags.items() if flags},
            }
            token = _serializer().dumps(payload)
            if len(token) > 3500:
                raise UnavailableError
            repository.update_login_record(db, row["user_id"], row["credential_version"])
            return IssuedSession(row["user_id"], SecretStr(token))

        return self._run(operation, write=True)

    def session_data(self, token: SecretStr) -> dict[str, Any] | None:
        """只接受本站有效簽章，拒絕舊 int Cookie、前台 Token、過期或竄改內容。"""
        raw = token.get_secret_value()
        if not raw or len(raw) > 3500:
            return None
        try:
            value = _serializer().loads(raw, max_age=settings.SESSION_MAX_AGE_SECONDS)
            if not isinstance(value, dict) or set(value) != {
                "v",
                "uid",
                "sid",
                "department",
                "cv",
                "expires",
                "functions",
            }:
                return None
            if value["v"] != 1 or type(value["cv"]) is not int or value["cv"] < 1:
                return None
            if (
                type(value["expires"]) is not int
                or value["expires"] <= datetime.now(UTC).timestamp()
            ):
                return None
            if not isinstance(value["functions"], dict) or any(
                not isinstance(code, str) or type(flags) is not int or not 0 <= flags <= 15
                for code, flags in value["functions"].items()
            ):
                return None
            for name in ["uid", "sid", "department"]:
                if not isinstance(value[name], str):
                    return None
                UUID(value[name])
            return value
        except (BadData, ValueError, TypeError, KeyError):
            return None

    def authenticate(self, token: SecretStr) -> Identity | None:
        """簽章驗證後再核對帳號與部門有效性；只讀取，不刷新登入時間。"""
        data = self.session_data(token)
        if data is None:
            return None
        identity = Identity(
            UUID(data["uid"]),
            UUID(data["sid"]),
            UUID(data["department"]),
            data["cv"],
            datetime.fromtimestamp(data["expires"], UTC),
            True,
            "admin",
        )
        try:
            return self._run(lambda db: self.current(db, identity))
        except UnauthenticatedError:
            return None

    def current(self, db: Session, identity: Identity) -> Identity:
        """失效帳號、離職、部門移除或憑證版本變更即拒絕，不讀身分／職稱。"""
        row = repository.current_account(db, identity.user_id)
        if not _valid_account(row) or row["credential_version"] != identity.credential_version:
            raise UnauthenticatedError
        if identity.expires_at <= datetime.now(UTC) or identity.selected_department_id not in {
            item["department_id"] for item in repository.departments(db, identity.user_id)
        }:
            raise UnauthenticatedError
        return identity

    def decision(
        self,
        db: Session,
        identity: Identity,
        code: str,
        action: Action,
        flags: dict[str, int] | None = None,
    ) -> Decision:
        """每次操作依目前部門最新勾選驗證；不聯集、不從查看推定修改。"""
        definition = next((item for item in self.registry if item.code == code), None)
        if definition is None or action not in definition.actions:
            raise DeniedError
        if flags is None:
            flags = self._flags(
                repository.functions(db, identity.user_id, identity.selected_department_id)
            )
        catalog_code = CATALOG_CODES.get(code, code)
        allowed = bool(flags.get(catalog_code, 0) & ACTION_FLAGS[action])
        # 配置頁仍維持其既有公司級字典／帳號範圍；業務資料不因此取得公司範圍。
        scope = (
            DataScope("company")
            if code in CATALOG_CODES
            else DataScope("departments", frozenset({identity.selected_department_id}))
        )
        return Decision(
            identity.user_id,
            identity.session_id,
            identity.selected_department_id,
            code,
            action,
            "unset",
            catalog_code in flags,
            "department-permissions/v1",
            identity.expires_at,
            scope if allowed else DataScope(),
            department_effect="allow" if allowed else "deny",
        )

    def decide(self, identity: Identity, code: str, action: Action) -> Decision:
        """由可信路由傳入固定操作，真正執行前讀目前部門權限。"""

        def operation(db: Session) -> Decision:
            repository.prepare(db)
            return self.decision(db, self.current(db, identity), code, action)

        return self._run(operation)

    def effective_access(self, identity: Identity) -> AccessSnapshot:
        """將部門勾選組合為 E 的選單／操作資料，不創建額外資格。"""

        def operation(db: Session) -> AccessSnapshot:
            repository.prepare(db)
            current = self.current(db, identity)
            departments = tuple(
                EffectiveDepartment(**row) for row in repository.departments(db, current.user_id)
            )
            flags = self._flags(
                repository.functions(db, current.user_id, current.selected_department_id)
            )
            decisions = tuple(
                self.decision(db, current, item.code, action, flags)
                for item in self.registry
                for action in item.actions
            )
            return AccessSnapshot(
                current.user_id,
                current.session_id,
                current.selected_department_id,
                "department-permissions/v1",
                current.expires_at,
                departments,
                decisions,
                True,
            )

        return self._run(operation)

    def sidebar_menu(self, identity: Identity) -> tuple[SidebarEntry, ...]:
        """即時讀目前部門的授權目錄；未知功能可顯示，不從代碼推導可執行網址。"""

        def operation(db: Session) -> tuple[SidebarEntry, ...]:
            repository.prepare(db)
            current = self.current(db, identity)
            rows = repository.functions(db, current.user_id, current.selected_department_id)
            flags = self._flags(rows)
            definitions = {
                CATALOG_CODES.get(item.code, item.code): item
                for item in self.registry
                if item.code != "sys.department-permissions"
            }
            entries = []
            for row in rows:
                code = row["function_code"]
                mask = flags.get(code, 0)
                if not mask or code == "welcome":
                    continue
                definition = definitions.get(code)
                can_open = bool(definition and Action.VIEW in definition.actions and mask & 1)
                entries.append(
                    SidebarEntry(
                        definition.code if definition else code,
                        row["function_name"],
                        definition.route if can_open else None,
                        "" if can_open else ("尚未接線" if definition is None else "未授權查看"),
                    )
                )
            # 維持既有管理入口順序，其他功能接續；帳號與登出仍相鄰。
            order = {"sys.departments": 0, "sys.features": 1, "sys.accounts": 3}
            return tuple(sorted(entries, key=lambda item: (order.get(item.code, 2), item.code)))

        return self._run(operation)

    def logout(self, token: SecretStr) -> None:
        """網站 Session 由路由清 Cookie 結束，不另外改寫 DB Session。"""

    def profile(self, identity: Identity) -> Profile:
        """沿用本人安全投影，不新增帳號維護邏輯。"""

        def operation(db: Session) -> Profile:
            return Profile(**repository.profile(db, self.current(db, identity).user_id))

        return self._run(operation)

    def update_profile(self, identity: Identity, patch: ProfilePatch) -> None:
        """本批只串登入；既有本人修改仍交原共同服務，不擅自新增寫入。"""
        raise UnavailableError

    def request_password_reset(self, email: str) -> None:
        """保留原 B 忘記密碼接點，不另造 Token 或假稱寄信。"""
        raise UnavailableError

    def change_password(self, identity: Identity, current: SecretStr, new: SecretStr) -> None:
        """保留原 B 改密碼接點，本批不新增密碼寫入。"""
        raise UnavailableError
