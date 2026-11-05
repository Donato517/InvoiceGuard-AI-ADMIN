"""測試目標：查無資料次數（rag_no_answer_stats）的 SQL 與列表統計區間；全部離線，以替身 Session 驗證。"""

from datetime import date
from typing import Any

from app.db.repositories import rag_repository as repo
from app.services import rag_admin_service


class _Result:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self.rows = rows or []
        self.scalar = scalar

    def mappings(self) -> list[dict[str, Any]]:
        return self.rows

    def scalar_one(self) -> Any:
        return self.scalar


class _Db:
    def __init__(self, *, table_exists: bool = True, rows: list[dict[str, Any]] | None = None):
        self.table_exists = table_exists
        self.rows = rows or []
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((statement, params or {}))
        if statement is repo.NO_ANSWER_TABLE_EXISTS:
            return _Result(scalar=self.table_exists)
        return _Result(rows=self.rows)


def test_increment_writes_each_knowledge_base_once_in_sorted_order() -> None:
    db = _Db()
    repo.increment_no_answer(db, ["kb-b", "kb-a", "kb-b"], date(2026, 11, 5))  # type: ignore[arg-type]
    assert [(s, p["id"]) for s, p in db.calls] == [
        (repo.INCREMENT_NO_ANSWER, "kb-a"),
        (repo.INCREMENT_NO_ANSWER, "kb-b"),
    ]
    assert all(p["stat_date"] == date(2026, 11, 5) for _, p in db.calls)


def test_increment_sql_only_adds_one_and_stores_no_content() -> None:
    sql = str(repo.INCREMENT_NO_ANSWER)
    assert "ON CONFLICT (stat_date, knowledge_base_id)" in sql
    assert "no_answer_count + 1" in sql
    # 只寫日期、知識庫與次數三欄，沒有問題、回答或使用者
    assert "(stat_date, knowledge_base_id, no_answer_count)" in sql
    for column in ("question", "answer_text", "user_id", "department_id"):
        assert column not in sql


def test_sum_returns_counts_by_knowledge_base() -> None:
    db = _Db(rows=[{"knowledge_base_id": "kb-1", "total": 3}])
    assert repo.sum_no_answer_since(db, date(2026, 10, 7)) == {"kb-1": 3}  # type: ignore[arg-type]
    assert db.calls[-1] == (repo.SUM_NO_ANSWER_SINCE, {"since": date(2026, 10, 7)})


def test_sum_is_empty_when_the_table_does_not_exist() -> None:
    db = _Db(table_exists=False)
    assert repo.sum_no_answer_since(db, date(2026, 10, 7)) == {}  # type: ignore[arg-type]
    assert [s for s, _ in db.calls] == [repo.NO_ANSWER_TABLE_EXISTS]


def test_department_qa_knowledge_bases_are_listed() -> None:
    db = _Db(rows=[{"knowledge_base_id": "kb-1"}, {"knowledge_base_id": "kb-2"}])
    assert repo.list_qa_knowledge_base_ids(db, "d1") == ["kb-1", "kb-2"]  # type: ignore[arg-type]
    assert "use_for_qa" in str(repo.LIST_QA_KNOWLEDGE_BASE_IDS)


def test_thirty_day_window_includes_today() -> None:
    assert rag_admin_service.NO_ANSWER_DAYS == 30
    assert rag_admin_service.no_answer_since(date(2026, 11, 5)) == date(2026, 10, 7)
