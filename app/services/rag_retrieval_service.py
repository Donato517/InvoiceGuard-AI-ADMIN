"""
模組名稱：app.services.rag_retrieval_service
功能說明：RAG 檢索——問題轉向量、以 rag_repository（pgvector）依相似度查詢，再確認四項條件並整理來源文件與版本。
相依模組：app.schemas.rag、app.db.repositories.rag_repository、app.integrations.vertex_embedding
注意事項：
    - 不開 API 端點，供程式內部與 ADMIN 問答呼叫；不依賴畫面，日後可搬到 API 站。
    - 相似度由 DB 計算與排序（embedding <=> 查詢向量），service 不自行計算相似度。
    - 四項條件（知識庫 approved 且 is_active、部門 use_for_qa、文件 is_approved、parse_status = ready）在 SQL 中套用；
      知識庫與部門條件在檔案範圍 CTE 彙整，repository 回傳固定值，service 實際再確認的是文件狀態與 embedding_model。
    - 權限以已驗證的 user_id 與目前部門查 v_user_department_rag_access，不接受請求自報。
    - 先取得問題向量再開 DB 連線（呼叫模型期間不持有 DB 交易）；本流程只讀，不 commit。
"""

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.repositories import rag_repository
from app.integrations.vertex_embedding import VertexEmbeddingClient
from app.schemas.rag import (
    APPROVAL_APPROVED,
    PARSE_READY,
    RagRuleError,
    RetrievalCandidate,
    SearchHit,
    SearchResult,
    SourceDocument,
)

DEFAULT_TOP_K = 5
MAX_TOP_K = 20
# 問題轉向量的時間預算（ADMIN 函式上限 60 秒，保留回答生成時間）
QUERY_TIMEOUT_SECONDS = 5.0


def exclusion_reasons(candidate: RetrievalCandidate, embedding_model: str) -> list[str]:
    """列出候選段落不符合檢索條件的原因；空清單代表可採用。

    Args:
        candidate: repository 回傳的段落。
        embedding_model: 查詢向量使用的模型。

    Returns:
        不符合的條件代碼。
    """
    reasons = []
    if (
        candidate.approval_status != APPROVAL_APPROVED
        or not candidate.is_active
        or candidate.file_count < 1
    ):
        reasons.append("kb_not_available")
    if not candidate.use_for_qa or not candidate.can_read:
        reasons.append("department_not_using_kb")
    if not candidate.is_approved:
        reasons.append("document_not_approved")
    if candidate.parse_status != PARSE_READY:
        reasons.append("document_not_ready")
    if candidate.embedding_model != embedding_model:
        reasons.append("embedding_model_mismatch")
    return reasons


def build_result(
    candidates: list[RetrievalCandidate], embedding_model: str, top_k: int
) -> SearchResult:
    """排除不符條件的段落，保留 DB 的相似度排序並整理來源文件。

    Args:
        candidates: repository 依距離排序的段落。
        embedding_model: 查詢向量使用的模型。
        top_k: 最多取幾段。

    Returns:
        檢索結果。
    """
    eligible = [c for c in candidates if not exclusion_reasons(c, embedding_model)]
    accepted = eligible[:top_k]
    hits = tuple(
        SearchHit(
            content=c.content,
            distance=c.distance,
            file_title=c.file_title,
            file_version=c.file_version,
            chunk_index=c.chunk_index,
            knowledge_name=c.knowledge_name,
            rag_file_id=c.rag_file_id,
            knowledge_names=c.knowledge_names,
        )
        for c in accepted
    )
    sources: dict[tuple[str, int], SourceDocument] = {}
    for c in accepted:
        sources.setdefault(
            (c.rag_file_id, c.file_version),
            SourceDocument(
                c.rag_file_id, c.file_title, c.file_version, c.knowledge_name, c.knowledge_names
            ),
        )
    return SearchResult(
        hits=hits,
        sources=tuple(sources.values()),
        excluded_count=len(candidates) - len(eligible),
    )


def search(
    question: str,
    user_id: UUID,
    department_id: UUID,
    top_k: int,
    client: VertexEmbeddingClient,
    open_session: Callable[[], Session] = rag_repository.open_session,
    timeout_seconds: float = QUERY_TIMEOUT_SECONDS,
) -> SearchResult:
    """依目前部門選用的知識庫檢索與問題最相關的段落。

    Args:
        question: 使用者問題。
        user_id: 已驗證身分的 user_id。
        department_id: 已驗證身分的目前選取部門。
        top_k: 最多取幾段（1～20）。
        client: 向量用戶端（task=query）。
        open_session: 開啟 DB 連線（測試替換為替身）。
        timeout_seconds: 問題轉向量的時間上限。

    Returns:
        檢索結果：保留 DB 的相似度排序，附來源文件與版本；部門沒有選用知識庫時為空結果。

    Raises:
        RagRuleError: 問題空白。
        EmbeddingError: 問題轉向量失敗（由呼叫端顯示或重試）。
    """
    text = question.strip()
    if not text:
        raise RagRuleError("QUESTION_REQUIRED", "請輸入問題")
    limit = max(1, min(int(top_k), MAX_TOP_K))
    batch = client.embed_texts([text], task="query", timeout_seconds=timeout_seconds)
    db = open_session()
    try:
        if not rag_repository.has_qa_access(db, user_id, department_id):
            return SearchResult(hits=(), sources=())
        candidates = rag_repository.search_chunks(
            db, batch.vectors[0], batch.model, department_id, limit
        )
    finally:
        db.close()
    return build_result(candidates, batch.model, limit)
