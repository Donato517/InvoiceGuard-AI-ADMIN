"""
模組名稱：tests.unit.test_rag_retrieval_service
功能說明：RAG 檢索測試——問題轉向量後交給 repository、無選用權限時不查詢、五項條件逐一不成立時排除、
          模型不同排除、保留 DB 排序與整理來源。
注意事項：以確定性的假用戶端與替換的 repository 函式驗證流程，不連資料庫、不呼叫 Google。
"""

from collections.abc import Sequence
from dataclasses import replace
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.db.repositories import rag_repository
from app.integrations.vertex_embedding import EmbeddingBatch, EmbeddingTask
from app.schemas.rag import (
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    PARSE_FAILED,
    PARSE_UPLOADED,
    RagRuleError,
    RetrievalCandidate,
    SearchResult,
)
from app.services.rag_retrieval_service import exclusion_reasons, search

MODEL = "synthetic-model"
USER = UUID("00000000-0000-4000-8000-000000000001")
FINANCE = UUID("00000000-0000-4000-8000-0000000000f1")
GOOD = RetrievalCandidate(
    chunk_id="c-1",
    rag_file_id="f-1",
    chunk_index=0,
    content="第三點 營業人進銷項資料檔格式如附件五。",
    distance=0.12,
    embedding_model=MODEL,
    file_title="營業稅電子資料申報繳稅作業要點",
    file_version=2,
    knowledge_base_id="kb-tax",
    knowledge_name="營業稅申報",
    approval_status="approved",
    is_active=True,
    use_for_qa=True,
    use_document=True,
    is_approved=True,
    parse_status="ready",
)


class FakeEmbedding:
    def __init__(self) -> None:
        self.tasks: list[str] = []

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        task: EmbeddingTask = "document",
        title: str | None = None,
        timeout_seconds: float | None = None,
    ) -> EmbeddingBatch:
        self.tasks.append(task)
        return EmbeddingBatch(vectors=((0.1, 0.2),), model=MODEL, dimensions=2)


class Repo:
    """替換 rag_repository 的 has_qa_access／search_chunks，記錄呼叫參數。"""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        candidates: list[RetrievalCandidate],
        has_access: bool = True,
    ) -> None:
        self.calls: list[tuple[tuple[float, ...], str, UUID, int]] = []
        self.session = MagicMock()
        monkeypatch.setattr(rag_repository, "has_qa_access", lambda db, u, d: has_access)

        def search_chunks(
            db: object, vector: Sequence[float], model: str, dept: UUID, top_k: int
        ) -> list[RetrievalCandidate]:
            self.calls.append((tuple(vector), model, dept, top_k))
            return candidates

        monkeypatch.setattr(rag_repository, "search_chunks", search_chunks)

    def open(self) -> MagicMock:
        return self.session


def _search(repo: Repo, top_k: int = 5, question: str = "附件五是什麼？") -> SearchResult:
    return search(question, USER, FINANCE, top_k, FakeEmbedding(), open_session=repo.open)


def test_search_passes_query_vector_and_keeps_db_order(monkeypatch: pytest.MonkeyPatch) -> None:
    second = replace(GOOD, chunk_id="c-2", chunk_index=1, distance=0.3)
    repo = Repo(monkeypatch, [GOOD, second])
    embedding = FakeEmbedding()
    result = search(" 附件五是什麼？ ", USER, FINANCE, 5, embedding, open_session=repo.open)
    assert embedding.tasks == ["query"]
    assert repo.calls == [((0.1, 0.2), MODEL, FINANCE, 5)]
    assert [h.chunk_index for h in result.hits] == [0, 1]
    assert [(s.rag_file_id, s.file_version) for s in result.sources] == [("f-1", 2)]
    assert result.excluded_count == 0
    repo.session.close.assert_called_once()
    repo.session.commit.assert_not_called()


def test_no_qa_access_returns_empty_without_searching(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repo(monkeypatch, [GOOD], has_access=False)
    result = _search(repo)
    assert result.hits == () and repo.calls == []


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"approval_status": APPROVAL_PENDING}, "kb_not_available"),
        ({"approval_status": APPROVAL_REJECTED}, "kb_not_available"),
        ({"is_active": False}, "kb_not_available"),
        ({"use_for_qa": False}, "department_not_using_kb"),
        ({"use_document": False}, "document_not_used"),
        ({"is_approved": False}, "document_not_approved"),
        ({"parse_status": PARSE_UPLOADED}, "document_not_ready"),
        ({"parse_status": PARSE_FAILED}, "document_not_ready"),
        ({"embedding_model": "old-model"}, "embedding_model_mismatch"),
    ],
)
def test_each_failed_condition_excludes_chunk(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, object], reason: str
) -> None:
    bad = replace(GOOD, chunk_id="c-bad", **changes)
    assert exclusion_reasons(bad, MODEL) == [reason]
    result = _search(Repo(monkeypatch, [bad, GOOD]))
    assert [h.content for h in result.hits] == [GOOD.content]
    assert result.excluded_count == 1


def test_good_candidate_has_no_exclusion() -> None:
    assert exclusion_reasons(GOOD, MODEL) == []


def test_top_k_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repo(monkeypatch, [])
    _search(repo, top_k=999)
    _search(repo, top_k=0)
    assert [call[3] for call in repo.calls] == [20, 1]


def test_blank_question_rejected_before_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = Repo(monkeypatch, [])
    with pytest.raises(RagRuleError):
        _search(repo, question="  ")
    assert repo.calls == []
