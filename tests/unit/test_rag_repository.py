"""
模組名稱：tests.unit.test_rag_repository
功能說明：rag_repository 固定 SQL 測試——向量文字格式、檢索 SQL 的五項條件與 pgvector 運算子、參數與結果對應、不 commit。
注意事項：以 MagicMock 取代 Session，不連資料庫；SQL 是否能在正式 DB 執行尚未實測。
"""

from unittest.mock import MagicMock
from uuid import UUID

from app.db.repositories import rag_repository
from app.schemas.rag import IndexedChunk

DEPT = UUID("00000000-0000-4000-8000-0000000000f1")


def test_vector_literal() -> None:
    assert rag_repository.vector_literal((0.5, -1, 2.25)) == "[0.5,-1.0,2.25]"


def test_search_sql_applies_five_conditions_and_pgvector_order() -> None:
    sql = str(rag_repository.SEARCH_CHUNKS)
    for condition in (
        "kb.approval_status = 'approved' AND kb.is_active",
        "da.use_for_qa",
        "u.use_document",
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
        "knowledge_base_id": "kb",
        "knowledge_name": "營業稅申報",
        "approval_status": "approved",
        "is_active": True,
        "use_for_qa": True,
        "use_document": True,
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


def test_replace_chunks_deletes_then_inserts_vectors() -> None:
    db = MagicMock()
    chunks = [IndexedChunk(0, "a", (1.0, 2.0), "m"), IndexedChunk(1, "b", (3.0, 4.0), "m")]
    rag_repository.replace_chunks(db, "f-1", chunks)
    statements = [call.args[0] for call in db.execute.call_args_list]
    assert statements[0] is rag_repository.DELETE_CHUNKS
    assert statements[1:] == [rag_repository.INSERT_CHUNK] * 2
    assert db.execute.call_args_list[2].args[1]["embedding"] == "[3.0,4.0]"
    assert "CAST(:embedding AS extensions.vector)" in str(rag_repository.INSERT_CHUNK)
    db.commit.assert_not_called()


def test_parse_status_update_is_guarded_by_version() -> None:
    assert "file_version = :file_version" in str(rag_repository.UPDATE_PARSE_STATUS)
    assert "FOR UPDATE" in str(rag_repository.LOCK_FILE_VERSION)
    assert "SKIP LOCKED" in str(rag_repository.LOCK_JOB)
