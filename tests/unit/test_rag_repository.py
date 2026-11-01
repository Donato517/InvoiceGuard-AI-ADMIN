"""
模組名稱：tests.unit.test_rag_repository
功能說明：rag_repository 固定 SQL 測試——向量文字格式、檢索 SQL 的四項條件與 pgvector 運算子、參數與結果對應、不 commit。
注意事項：以 MagicMock 取代 Session，不連資料庫；SQL 是否能在正式 DB 執行尚未實測。
"""

import re
from unittest.mock import MagicMock
from uuid import UUID

from app.db.repositories import rag_repository
from app.schemas.rag import IndexedChunk

DEPT = UUID("00000000-0000-4000-8000-0000000000f1")


def test_vector_literal() -> None:
    assert rag_repository.vector_literal((0.5, -1, 2.25)) == "[0.5,-1.0,2.25]"


def test_search_sql_applies_four_conditions_and_pgvector_order() -> None:
    sql = str(rag_repository.SEARCH_CHUNKS)
    for condition in (
        "kb.approval_status = 'approved' AND kb.is_active",
        "da.use_for_qa",
        "f.is_approved",
        "f.parse_status = 'ready'",
        "c.embedding_model = :embedding_model",
    ):
        assert condition in sql
    assert "OPERATOR(extensions.<=>) CAST(:query_embedding AS extensions.vector)" in sql
    assert "ORDER BY distance" in sql


def test_search_chunks_binds_parameters_and_maps_rows() -> None:
    row = {
        "chunk_id": "c-1",
        "rag_file_id": "f-1",
        "chunk_index": 0,
        "content": "第一點",
        "distance": "0.25",
        "embedding_model": "m",
        "file_title": "要點",
        "file_version": 1,
        "knowledge_base_ids": ["kb"],
        "knowledge_names": ["營業稅申報"],
        "can_read": True,
        "file_count": 1,
        "approval_status": "approved",
        "is_active": True,
        "use_for_qa": True,
        "is_approved": True,
        "parse_status": "ready",
    }
    db = MagicMock()
    db.execute.return_value.mappings.return_value = [row]
    [candidate] = rag_repository.search_chunks(db, (0.1, 0.2), "m", DEPT, 5)
    params = db.execute.call_args.args[1]
    assert params == {
        "query_embedding": "[0.1,0.2]",
        "embedding_model": "m",
        "department_id": str(DEPT),
        "top_k": 5,
    }
    assert candidate.distance == 0.25
    db.commit.assert_not_called()


def test_get_job_does_not_lock_and_lock_job_skips_locked_rows() -> None:
    assert "FOR UPDATE" not in str(rag_repository.GET_JOB)
    assert "FOR UPDATE SKIP LOCKED" in str(rag_repository.LOCK_JOB)


def test_department_code_lookup_includes_inactive_departments() -> None:
    assert "is_active" not in str(rag_repository.GET_DEPARTMENT_CODE)
    assert "is_active" in str(rag_repository.GET_DEPARTMENT)
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = "finance_d"
    assert rag_repository.get_department_code(db, "dept") == "finance_d"
    db.execute.return_value.scalar_one_or_none.return_value = None
    assert rag_repository.get_department_code(db, "dept") is None


def test_list_all_file_references_groups_by_file_and_keeps_query_order() -> None:
    sql = str(rag_repository.LIST_ALL_FILE_REFERENCES)
    assert "public.rag_knowledge_base_files" in sql and "ORDER BY kb.knowledge_name" in sql
    db = MagicMock()
    db.execute.return_value.mappings.return_value = [
        {"rag_file_id": "f-1", "knowledge_base_id": "kb-a", "knowledge_name": "甲"},
        {"rag_file_id": "f-2", "knowledge_base_id": "kb-a", "knowledge_name": "甲"},
        {"rag_file_id": "f-1", "knowledge_base_id": "kb-b", "knowledge_name": "乙"},
    ]
    references = rag_repository.list_all_file_references(db)
    assert [k.knowledge_name for k in references["f-1"]] == ["甲", "乙"]
    assert [k.knowledge_base_id for k in references["f-2"]] == ["kb-a"]
    db.commit.assert_not_called()


def test_replace_chunks_deletes_then_inserts_vectors() -> None:
    db = MagicMock()
    chunks = [IndexedChunk(0, "a", (1.0, 2.0), "m"), IndexedChunk(1, "b", (3.0, 4.0), "m")]
    rag_repository.replace_chunks(db, "f-1", chunks)
    statements = [call.args[0] for call in db.execute.call_args_list]
    assert statements[0] is rag_repository.DELETE_CHUNKS
    assert statements[1:] == [rag_repository.INSERT_CHUNK]  # 一次批次寫入
    rows = db.execute.call_args_list[1].args[1]
    assert [row["chunk_index"] for row in rows] == [0, 1]
    assert rows[1]["embedding"] == "[3.0,4.0]"
    assert "CAST(:embedding AS extensions.vector)" in str(rag_repository.INSERT_CHUNK)
    db.commit.assert_not_called()


def test_parse_status_update_is_guarded_by_version() -> None:
    assert "file_version = :file_version" in str(rag_repository.UPDATE_PARSE_STATUS)
    assert "FOR UPDATE" in str(rag_repository.LOCK_FILE_VERSION)
    assert "SKIP LOCKED" in str(rag_repository.LOCK_JOB)


def test_both_department_searches_use_all_documents_without_file_usage_join() -> None:
    for statement in (rag_repository.SEARCH_CHUNKS, rag_repository.SEARCH_KEYWORDS):
        sql = str(statement)
        assert "rag_file_department_usage" not in sql
        assert "use_document" not in sql
        for condition in (
            "kb.approval_status = 'approved' AND kb.is_active",
            "da.use_for_qa",
            "f.is_approved",
            "f.parse_status = 'ready'",
        ):
            assert condition in sql
        assert "kb.knowledge_base_id = CAST(:id AS uuid)" not in sql


def test_all_searches_group_authorized_files_before_chunk_limit() -> None:
    for sql in map(
        str,
        (
            rag_repository.SEARCH_CHUNKS,
            rag_repository.SEARCH_KEYWORDS,
            rag_repository.SEARCH_REVIEW_CHUNKS,
            rag_repository.SEARCH_REVIEW_KEYWORDS,
        ),
    ):
        assert (
            sql.index("GROUP BY l.rag_file_id")
            < sql.index("JOIN public.rag_chunks")
            < sql.index("LIMIT :top_k")
        )
        assert "array_agg(kb.knowledge_name" in sql and "kb.file_count >= 1" in sql
        assert "kb.approval_status = 'approved'" in sql and "kb.is_active" in sql
        assert "f.is_approved" in sql and "f.parse_status = 'ready'" in sql
        assert not re.search(r"\bf\.knowledge_base_id\b", sql)


def test_shared_review_hit_maps_all_knowledge_names_once() -> None:
    row = {
        "content": "條文",
        "distance": 0.1,
        "file_title": "來源文件",
        "file_version": 1,
        "chunk_index": 0,
        "knowledge_names": ["A", "B"],
        "rag_file_id": "f",
    }
    db = MagicMock()
    db.execute.return_value.mappings.return_value = [row]
    [hit] = rag_repository.search_review_chunks(db, [0.1], "m", ["kb", "kb-b"], 8)
    assert hit.knowledge_names == ("A", "B") and hit.knowledge_name == "A、B"
    assert db.execute.call_args.args[1]["ids"] == ["kb", "kb-b"]
    db.commit.assert_not_called()


def test_test_scope_uses_list_ids_and_never_department_access() -> None:
    for statement in (rag_repository.SEARCH_REVIEW_CHUNKS, rag_repository.SEARCH_REVIEW_KEYWORDS):
        sql = str(statement)
        assert "ANY(CAST(:ids AS uuid[]))" in sql
        assert "rag_department_access" not in sql
        assert "f.is_approved" in sql and "f.parse_status = 'ready'" in sql
        assert sql.index("GROUP BY l.rag_file_id") < sql.index("JOIN public.rag_chunks")
        assert "array_agg(kb.knowledge_name" in sql
    db = MagicMock()
    db.execute.return_value.mappings.return_value = []
    rag_repository.search_keyword_chunks(db, ["第十二條"], 4, knowledge_base_ids=("a", "b"))
    assert db.execute.call_args.args[1] == {"patterns": ["第十二條"], "top_k": 4, "ids": ["a", "b"]}


def test_new_file_insert_omits_transitional_knowledge_base_column() -> None:
    from app.schemas.rag import RagFile

    file = RagFile(
        "f",
        "獨立檔案",
        source_type="other",
        effective_date_status="unknown",
        content_sha256="a" * 64,
    )
    db = MagicMock()
    rag_repository.insert_file(db, file, "u")
    statement, params = db.execute.call_args.args
    assert "knowledge_base_id" not in str(statement) and "knowledge_base_id" not in params
    assert params["source_type"] == "other" and params["content_sha256"] == "a" * 64
    assert "file_count" not in str(statement)
    db.commit.assert_not_called()


def test_association_queries_and_indexing_use_link_table_and_never_write_counter() -> None:
    for statement in (
        rag_repository.LIST_FILES,
        rag_repository.LIST_LATEST_JOBS,
        rag_repository.LIST_FILES_TO_INDEX,
        rag_repository.QUEUE_REINDEX,
        rag_repository.LIST_FILE_REFERENCES,
    ):
        sql = str(statement)
        assert "rag_knowledge_base_files" in sql
        assert not re.search(r"\bf\.knowledge_base_id\b", sql)
    for statement in (
        rag_repository.INSERT_FILE_LINK,
        rag_repository.DELETE_FILE_LINK,
        rag_repository.INSERT_FILE,
        rag_repository.REPLACE_FILE_CONTENT,
    ):
        assert "file_count" not in str(statement)
    assert "ON CONFLICT (knowledge_base_id, rag_file_id) DO NOTHING" in str(
        rag_repository.INSERT_FILE_LINK
    )
