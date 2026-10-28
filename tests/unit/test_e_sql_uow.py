"""E SQL adapter 離線測試：驗證交易生命週期與查詢範圍，不連 PostgreSQL。"""

from collections.abc import Iterator
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.repositories.e_repository import DepartmentRepository
from app.schemas.e_admin import (
    ConflictError,
    DataScope,
    DepartmentInput,
    FeatureInput,
    MissingError,
    UnavailableError,
)
from app.services import e_sql_uow

ACTOR = UUID("00000000-0000-4000-8000-000000000001")
DEPT = UUID("00000000-0000-4000-8000-000000000002")
OTHER_DEPT = UUID("00000000-0000-4000-8000-000000000003")


@pytest.fixture
def sql_work(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[e_sql_uow.SqlUnitOfWork, MagicMock, MagicMock]]:
    session = MagicMock()
    journal = MagicMock()
    monkeypatch.setattr(e_sql_uow, "open_session", lambda: session)
    yield e_sql_uow.SqlUnitOfWork(journal), session, journal


def test_sql_read_always_rolls_back_and_closes(sql_work: tuple) -> None:
    work, session, _ = sql_work
    with work:
        pass
    session.commit.assert_not_called()
    session.rollback.assert_called_once()
    session.close.assert_called_once()


def test_sql_write_commits_once_and_closes(sql_work: tuple) -> None:
    work, session, _ = sql_work
    with work:
        work.commit()
    session.commit.assert_called_once()
    session.rollback.assert_not_called()
    session.close.assert_called_once()


@pytest.mark.parametrize(
    "error",
    [
        IntegrityError("synthetic-secret-sql-marker", {}, Exception("synthetic-private-data")),
        OperationalError("synthetic-secret-sql-marker", {}, Exception("synthetic-private-data")),
    ],
)
def test_commit_failure_rolls_back_without_leaking_sql(sql_work: tuple, error: Exception) -> None:
    work, session, _ = sql_work
    session.commit.side_effect = error
    expected = ConflictError if isinstance(error, IntegrityError) else UnavailableError
    with pytest.raises(expected) as caught, work:
        work.commit()
    assert "synthetic-" not in str(caught.value)
    assert caught.value.__suppress_context__
    session.rollback.assert_called_once()
    session.close.assert_called_once()


def test_journal_uses_same_transaction_and_failure_rolls_back(sql_work: tuple) -> None:
    work, session, journal = sql_work
    journal.authorize.side_effect = RuntimeError("synthetic-private-data")
    with pytest.raises(UnavailableError), work:
        work.authorize(MagicMock(), MagicMock())
    assert journal.authorize.call_args.args[0] is session
    session.commit.assert_not_called()
    session.rollback.assert_called_once()


def test_scoped_department_query_filters_before_projection(sql_work: tuple) -> None:
    _, session, journal = sql_work
    session.execute.return_value.mappings.return_value = []
    repository = DepartmentRepository(session, journal)
    repository.list(DataScope("departments", frozenset({DEPT})))
    statement, values = session.execute.call_args.args
    assert "WHERE department_id = ANY" in str(statement)
    assert values == {"department_ids": [DEPT]}
    assert str(DEPT) not in str(statement)
    assert OTHER_DEPT not in values["department_ids"]


@pytest.mark.parametrize("kind", ["none", "own"])
def test_non_department_scope_reads_no_directory_rows(sql_work: tuple, kind: str) -> None:
    _, session, journal = sql_work
    session.execute.return_value.mappings.return_value = []
    DepartmentRepository(session, journal).list(DataScope(kind))
    statement = session.execute.call_args.args[0]
    assert "WHERE FALSE" in str(statement)


def test_disable_impact_counts_disabled_account_primary_references(sql_work: tuple) -> None:
    _, session, journal = sql_work
    session.execute.return_value.mappings.return_value.one.return_value = {
        "current_members": 0,
        "primary_references": 1,
        "live_sessions": 0,
    }
    impact = DepartmentRepository(session, journal).impact(DEPT)
    statement, values = session.execute.call_args.args
    assert impact.blocked
    assert "account_status = 'active'" not in str(statement)
    assert values == {"department_id": DEPT}


def test_owner_scope_requires_server_loaded_owner() -> None:
    from app.schemas.e_admin import Target

    assert DataScope("own").covers(ACTOR, Target(owner_id=ACTOR))
    assert not DataScope("own").covers(ACTOR, Target(owner_id=OTHER_DEPT))
    assert not DataScope("own").covers(ACTOR, Target(global_resource=True))


@pytest.mark.parametrize("resource", ["departments", "features"])
def test_missing_sql_resource_returns_404_and_closes_without_commit(
    sql_work: tuple,
    resource: str,
) -> None:
    work, session, _ = sql_work
    session.execute.return_value.mappings.return_value.one_or_none.return_value = None
    with pytest.raises(MissingError), work:
        if resource == "departments":
            work.departments.get(DEPT, DataScope("company"), lock=True)
        else:
            work.features.get(DEPT, lock=True)
    assert "FOR UPDATE" in str(session.execute.call_args.args[0])
    session.commit.assert_not_called()
    session.rollback.assert_called_once()
    session.close.assert_called_once()


@pytest.mark.parametrize("operation", ["department_update", "feature_insert"])
def test_unique_constraint_failure_is_sanitized_and_rolled_back(
    sql_work: tuple,
    operation: str,
) -> None:
    work, session, _ = sql_work
    session.execute.side_effect = IntegrityError(
        "synthetic-private-statement", {}, Exception("synthetic-private-value")
    )
    with pytest.raises(ConflictError) as caught, work:
        if operation == "department_update":
            work.departments.update(
                DEPT, DepartmentInput(department_code="SYS", department_name="重複代碼")
            )
        else:
            work.features.insert(
                FeatureInput(function_code="sys.features", function_name="重複功能")
            )
    assert "synthetic-" not in str(caught.value)
    session.commit.assert_not_called()
    session.rollback.assert_called_once()
    session.close.assert_called_once()
