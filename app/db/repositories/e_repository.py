"""模組名稱：app.db.repositories.e_repository；功能說明：v1.3 部門與功能的固定參數化 SQL。

不更改共用 ORM／DDL，不提交交易；revision 由 G／DB 核定的 journal 提供。
"""

from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_engine
from app.schemas.e_admin import (
    ChangeEvent,
    ConflictError,
    DataScope,
    Decision,
    Department,
    DepartmentImpact,
    DepartmentInput,
    EError,
    Feature,
    FeatureInput,
    Identity,
    MissingError,
    UnavailableError,
)


def open_session() -> Session:
    """由既有 DB 模組開啟 Session；不傳遞底層設定或連線錯誤文字。"""
    try:
        return SessionLocal(bind=get_engine())
    except Exception:
        raise UnavailableError from None


def safe_store_error(error: BaseException | None) -> EError | None:
    """移除底層參數與 SQL 內容；不截斷 KeyboardInterrupt 等程序控制。"""
    if isinstance(error, EError):
        return None
    if isinstance(error, IntegrityError):
        return ConflictError()
    if isinstance(error, Exception):
        return UnavailableError()
    return None


class JournalPort(Protocol):
    """由 G／DB 提供的正式接點；與業務寫入共用傳入的 Session，禁止自行 commit。"""

    def revision(self, session: Session, kind: str, resource_id: UUID) -> str:
        """讀取已持久化的資源版本，新資源可用專用初始版本。"""
        ...

    def authorize(self, session: Session, identity: Identity, decision: Decision) -> None:
        """交易內鎖定並重驗憑證、管理職務、功能及政策版本。"""
        ...

    def append(self, session: Session, event: ChangeEvent) -> None:
        """同交易 CAS、audit、最後管理者保護、新版本及失效事件；錯誤必須拋出。"""
        ...


class DepartmentRepository:
    """固定表名與欄位；scope 在 SELECT 套用，不能先讀所有資料再交給模板過濾。"""

    def __init__(self, session: Session, journal: JournalPort) -> None:
        self.session = session
        self.journal = journal

    def list(self, scope: DataScope) -> list[Department]:
        """依核定範圍讀部門安全欄位；不讀帳號列。"""
        where, values = self._scope(scope)
        rows = self.session.execute(
            text(
                "SELECT department_id, department_code, department_name, is_active "
                "FROM public.departments WHERE " + where + " ORDER BY department_code"
            ),
            values,
        ).mappings()
        return [self._project(dict(row)) for row in rows]

    def get(self, department_id: UUID, scope: DataScope, *, lock: bool = False) -> Department:
        """取得並視需要鎖定範圍內目標；範圍外與不存在都回 MissingError。"""
        where, values = self._scope(scope)
        values["department_id"] = department_id
        statement = (
            "SELECT department_id, department_code, department_name, is_active "
            "FROM public.departments WHERE department_id = :department_id AND " + where
        )
        if lock:
            statement += " FOR UPDATE"
        row = self.session.execute(text(statement), values).mappings().one_or_none()
        if row is None:
            raise MissingError
        return self._project(dict(row))

    def insert(self, data: DepartmentInput) -> Department:
        """新增，唯一約束競爭交由 DB 判斷，再由 service 轉為安全錯誤。"""
        row = (
            self.session.execute(
                text(
                    "INSERT INTO public.departments (department_code, department_name, is_active) "
                    "VALUES (:department_code, :department_name, :is_active) "
                    "RETURNING department_id, department_code, department_name, is_active"
                ),
                data.model_dump(),
            )
            .mappings()
            .one()
        )
        return self._project(dict(row))

    def update(self, department_id: UUID, data: DepartmentInput) -> Department:
        """只更新 E 管理欄位；updated_at 沿用既有 Trigger。"""
        row = (
            self.session.execute(
                text(
                    "UPDATE public.departments SET department_code = :department_code, "
                    "department_name = :department_name, is_active = :is_active "
                    "WHERE department_id = :department_id "
                    "RETURNING department_id, department_code, department_name, is_active"
                ),
                data.model_dump() | {"department_id": department_id},
            )
            .mappings()
            .one()
        )
        return self._project(dict(row))

    def impact(self, department_id: UUID) -> DepartmentImpact:
        """檢查當前成員、所有主要部門參照、尚未過期或撤銷的 Session。"""
        row = (
            self.session.execute(
                text("""
            SELECT
              (SELECT count(*) FROM public.user_departments
               WHERE department_id = :department_id AND unassigned_at IS NULL) AS current_members,
              (SELECT count(*) FROM invoiceguard_identity.user_accounts
               WHERE primary_department_id = :department_id) AS primary_references,
              (SELECT count(*) FROM invoiceguard_identity.user_sessions
               WHERE selected_department_id = :department_id AND revoked_at IS NULL
                 AND expires_at > now()) AS live_sessions
        """),
                {"department_id": department_id},
            )
            .mappings()
            .one()
        )
        return DepartmentImpact(**dict(row))

    @staticmethod
    def _scope(scope: DataScope) -> tuple[str, dict[str, object]]:
        if scope.kind == "company":
            return "TRUE", {}
        if scope.kind == "departments" and scope.department_ids:
            return "department_id = ANY(CAST(:department_ids AS uuid[]))", {
                "department_ids": list(scope.department_ids),
            }
        return "FALSE", {}

    def _project(self, row: dict[str, object]) -> Department:
        return Department(
            **row,
            revision=self.journal.revision(
                self.session,
                "department",
                row["department_id"],
            ),
        )


class FeatureRepository:
    """功能表沒有 revision 欄位，不以虛構欄位或 updated_at 取代 journal。"""

    def __init__(self, session: Session, journal: JournalPort) -> None:
        self.session = session
        self.journal = journal

    def list(self) -> list[Feature]:
        """公司級字典的查詢須先通過 service 全域 guard。"""
        rows = self.session.execute(
            text(
                "SELECT function_id, function_code, function_name, app_scope, is_active "
                "FROM public.app_functions ORDER BY function_code"
            )
        ).mappings()
        return [self._project(dict(row)) for row in rows]

    def get(self, function_id: UUID, *, lock: bool = False) -> Feature:
        """取得功能，更新前鎖定。"""
        statement = (
            "SELECT function_id, function_code, function_name, app_scope, is_active "
            "FROM public.app_functions WHERE function_id = :function_id"
        )
        if lock:
            statement += " FOR UPDATE"
        row = (
            self.session.execute(text(statement), {"function_id": function_id})
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise MissingError
        return self._project(dict(row))

    def insert(self, data: FeatureInput) -> Feature:
        """新增字典，不自動建立 grants。"""
        row = (
            self.session.execute(
                text(
                    "INSERT INTO public.app_functions (function_code, function_name, app_scope, is_active) "
                    "VALUES (:function_code, :function_name, :app_scope, :is_active) "
                    "RETURNING function_id, function_code, function_name, app_scope, is_active"
                ),
                data.model_dump(),
            )
            .mappings()
            .one()
        )
        return self._project(dict(row))

    def update(self, function_id: UUID, data: FeatureInput) -> Feature:
        """功能代碼保持不變，避免舊授權改指其他能力。"""
        row = (
            self.session.execute(
                text(
                    "UPDATE public.app_functions SET function_name = :function_name, "
                    "app_scope = :app_scope, is_active = :is_active WHERE function_id = :function_id "
                    "RETURNING function_id, function_code, function_name, app_scope, is_active"
                ),
                data.model_dump() | {"function_id": function_id},
            )
            .mappings()
            .one()
        )
        return self._project(dict(row))

    def _project(self, row: dict[str, object]) -> Feature:
        return Feature(
            **row,
            revision=self.journal.revision(
                self.session,
                "feature",
                row["function_id"],
            ),
        )
