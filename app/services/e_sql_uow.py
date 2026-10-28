"""模組名稱：app.services.e_sql_uow；功能說明：E 正式 SQL 交易適配器，需注入共同 journal。"""

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session

from app.db.repositories.e_repository import (
    DepartmentRepository,
    FeatureRepository,
    JournalPort,
    open_session,
    safe_store_error,
)
from app.schemas.e_admin import ChangeEvent, Decision, Identity, UnavailableError


class SqlUnitOfWork:
    """未獲 journal 不可建立，且不由預設 E factory 自動啟用。"""

    def __init__(self, journal: JournalPort) -> None:
        self.journal = journal
        self.session: Session | None = None
        self.committed = False

    def __enter__(self) -> Self:
        """延遲開啟單一 Session；read 交易結束也會 rollback／close。"""
        self.committed = False
        self.session = open_session()
        self.departments = DepartmentRepository(self.session, self.journal)
        self.features = FeatureRepository(self.session, self.journal)
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """所有 SQL 失敗轉固定錯誤；不讓參數、連線字串進 HTTP 或例外日誌。"""
        session = self._session()
        cleanup_failed = False
        try:
            if not self.committed:
                session.rollback()
        except Exception:
            cleanup_failed = True
        try:
            session.close()
        except Exception:
            cleanup_failed = True
        mapped = safe_store_error(error)
        if mapped is not None:
            raise mapped from None
        if cleanup_failed and error is None:
            raise UnavailableError from None

    def authorize(self, identity: Identity, decision: Decision) -> None:
        """journal 與帳號／G 在此交易共同保護政策版本與撤銷競爭。"""
        self.journal.authorize(self._session(), identity, decision)

    def record(self, event: ChangeEvent) -> None:
        """同交易 CAS、audit、版本及失效通知；不允許只寫 logger。"""
        self.journal.append(self._session(), event)

    def commit(self) -> None:
        """提交；延遲外鍵失敗仍由 __exit__ rollback。"""
        self._session().commit()
        self.committed = True

    def _session(self) -> Session:
        if self.session is None:
            raise UnavailableError
        return self.session
