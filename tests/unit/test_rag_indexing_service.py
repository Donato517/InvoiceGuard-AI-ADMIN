"""
模組名稱：tests.unit.test_rag_indexing_service
功能說明：RAG 索引測試——中文切段與條文邊界、txt／md 擷取與不支援格式、版本核對、掃描檔不可 ready、重試規則。
注意事項：
    - 只用自己寫的短文字 fixture，不使用真實法規檔案。
    - 向量以確定性的假用戶端（同 docs/RAG_DEVELOPMENT.md 的 FakeEmbedding）替代，不呼叫 Google。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.integrations.vertex_embedding import EmbeddingBatch, EmbeddingError, EmbeddingTask
from app.schemas.rag import (
    ERROR_EMPTY_TEXT,
    ERROR_TEXT_DECODE,
    ERROR_UNSUPPORTED_FORMAT,
    ERROR_VERSION_MISMATCH,
    INDEX_FAILED,
    INDEX_READY,
    INDEX_STALE,
    JOB_FAILED,
    JOB_PENDING,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    PARSE_FAILED,
    PARSE_PROCESSING,
    PARSE_READY,
    PARSE_UPLOADED,
    RagFile,
    RagJob,
    RagRuleError,
)
from app.services import rag_indexing_service as indexing
from app.services.rag_indexing_service import (
    PlainTextExtractor,
    TextExtractionError,
    index_file,
    is_retryable,
    prepare_chunks,
    record_failure,
    split_into_chunks,
    start_job,
)

NOW = datetime(2026, 10, 30, 22, 0, tzinfo=UTC)
LAW_TEXT = """營業稅電子資料申報繳稅作業要點（測試摘錄）
第一點 為便利營業人以電子資料申報營業稅，特訂定本要點。
第二點 營業人得以網際網路傳輸方式申報。
申報資料應依附件格式製作。
第三點 營業人進銷項資料檔格式如附件五。
"""


class FakeEmbedder:
    """確定性假向量：每段向量為 (字數, 段落序號)。"""

    def __init__(self, error: EmbeddingError | None = None, model: str = "synthetic-model") -> None:
        self.calls: list[list[str]] = []
        self.error = error
        self.model = model

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        task: EmbeddingTask = "document",
        title: str | None = None,
        timeout_seconds: float | None = None,
    ) -> EmbeddingBatch:
        assert task == "document" and timeout_seconds is not None and timeout_seconds > 0
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        vectors = tuple((float(len(t)), float(i)) for i, t in enumerate(texts))
        return EmbeddingBatch(vectors=vectors, model=self.model, dimensions=2)


def _running(version: int = 1) -> tuple[RagJob, RagFile]:
    rag_file = RagFile("f-1", "kb-1", "作業要點", file_version=version)
    return start_job(RagJob("job-1", "f-1"), rag_file, NOW)


# ---- 切段 ----


def test_empty_and_blank_text_give_no_chunks() -> None:
    assert split_into_chunks("") == []
    assert split_into_chunks(" \n \r\n ") == []
    assert prepare_chunks("   ") == ()


def test_article_boundaries_start_new_units_and_short_ones_merge() -> None:
    chunks = split_into_chunks(LAW_TEXT, size=60, overlap=10)
    assert all(len(c) <= 60 for c in chunks)
    # 每個條文起點都位於某一段的行首，不會從條文中間開始
    for article in ("第一點", "第二點", "第三點"):
        assert any(line.startswith(article) for c in chunks for line in c.split("\n"))
    # 第二點與其下一行屬同一單位，不被拆開
    assert any(
        "第二點 營業人得以網際網路傳輸方式申報。\n申報資料應依附件格式製作。" in c for c in chunks
    )


def test_whole_text_fits_in_one_chunk_when_size_is_large() -> None:
    chunks = split_into_chunks(LAW_TEXT, size=800, overlap=100)
    assert len(chunks) == 1
    assert chunks[0].startswith("營業稅電子資料申報繳稅作業要點")


def test_long_article_split_at_sentence_end_with_overlap() -> None:
    article = "第十條 " + "".join(f"第{i}句說明營業稅申報規定。" for i in range(1, 30))
    chunks = split_into_chunks(article, size=100, overlap=20)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    assert all(c.endswith("。") for c in chunks[:-1])
    # 相鄰兩段重疊：下一段開頭的文字出現在上一段結尾
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current[:5] in previous[-20:]


def test_numbered_items_and_attachments_are_boundaries() -> None:
    text = "說明如下：\n一、適用對象。\n二、申報期限。\n附件五 進銷項資料檔"
    chunks = split_into_chunks(text, size=12, overlap=2)
    assert "一、適用對象。" in chunks
    assert any(c.startswith("附件五") for c in chunks)


def test_chunk_size_validation() -> None:
    with pytest.raises(ValueError):
        split_into_chunks("abc", size=0)
    with pytest.raises(ValueError):
        split_into_chunks("abc", size=10, overlap=10)


def test_prepare_chunks_numbers_from_zero() -> None:
    drafts = prepare_chunks(LAW_TEXT, size=60, overlap=10)
    assert [d.chunk_index for d in drafts] == list(range(len(drafts)))
    assert all(d.char_count == len(d.content) for d in drafts)


# ---- 文字擷取（txt／md；其他格式等待核定解析套件）----


def test_plain_text_extractor_reads_txt_and_md() -> None:
    extractor = PlainTextExtractor()
    txt = extractor.extract("﻿第一點 目的".encode(), filename="要點.TXT", mime_type=None)
    assert txt.text == "第一點 目的"
    md = extractor.extract(
        "# 標題".encode(), filename="note", mime_type="text/markdown; charset=utf-8"
    )
    assert md.text == "# 標題"


@pytest.mark.parametrize(
    ("filename", "mime_type"),
    [("營業稅法.pdf", "application/pdf"), ("規範.docx", None), ("掃描.jpg", "image/jpeg")],
)
def test_pdf_and_other_formats_are_unsupported(filename: str, mime_type: str | None) -> None:
    with pytest.raises(TextExtractionError) as error:
        PlainTextExtractor().extract(b"%PDF-1.7", filename=filename, mime_type=mime_type)
    assert error.value.code == ERROR_UNSUPPORTED_FORMAT
    assert not is_retryable(error.value.code)


def test_non_utf8_text_fails() -> None:
    with pytest.raises(TextExtractionError) as error:
        PlainTextExtractor().extract("第一點".encode("big5"), filename="a.txt", mime_type=None)
    assert error.value.code == ERROR_TEXT_DECODE


# ---- 工作與索引 ----


def test_start_job_records_version_and_marks_processing() -> None:
    job, rag_file = _running(version=4)
    assert (job.job_status, job.attempts, job.file_version) == (JOB_RUNNING, 1, 4)
    assert rag_file.parse_status == PARSE_PROCESSING
    with pytest.raises(RagRuleError):
        start_job(job, rag_file, NOW)


def test_index_file_ready_when_version_unchanged() -> None:
    job, rag_file = _running()
    embedder = FakeEmbedder()
    result = index_file(
        job, rag_file, LAW_TEXT, embedder, lambda: 1, lambda: NOW, size=60, overlap=10
    )
    assert result.outcome == INDEX_READY
    assert result.parse_status == PARSE_READY
    assert result.job.job_status == JOB_SUCCEEDED
    assert [c.chunk_index for c in result.chunks] == list(range(len(result.chunks)))
    assert all(c.embedding_model == "synthetic-model" for c in result.chunks)
    assert result.chunks[0].embedding[0] == float(len(result.chunks[0].content))


def test_index_file_batches_embedding_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(indexing, "EMBED_BATCH_SIZE", 2)
    job, rag_file = _running()
    embedder = FakeEmbedder()
    result = index_file(
        job, rag_file, LAW_TEXT, embedder, lambda: 1, lambda: NOW, size=30, overlap=5
    )
    assert result.outcome == INDEX_READY
    assert all(len(call) <= 2 for call in embedder.calls)
    assert sum(len(call) for call in embedder.calls) == len(result.chunks)


def test_index_file_discards_result_when_version_changed() -> None:
    job, rag_file = _running(version=1)
    result = index_file(job, rag_file, LAW_TEXT, FakeEmbedder(), lambda: 2, lambda: NOW)
    assert result.outcome == INDEX_STALE
    assert result.chunks == ()
    assert result.parse_status is None
    assert (result.job.job_status, result.error_code) == (JOB_FAILED, ERROR_VERSION_MISMATCH)


def test_empty_text_fails_without_retry() -> None:
    job, rag_file = _running()
    embedder = FakeEmbedder()
    result = index_file(job, rag_file, "  \n ", embedder, lambda: 1, lambda: NOW)
    assert (result.outcome, result.error_code, result.parse_status) == (
        INDEX_FAILED,
        ERROR_EMPTY_TEXT,
        PARSE_FAILED,
    )
    assert result.job.job_status == JOB_FAILED
    assert embedder.calls == []


def test_retryable_embedding_error_returns_job_to_pending() -> None:
    job, rag_file = _running()
    result = index_file(
        job,
        rag_file,
        LAW_TEXT,
        FakeEmbedder(EmbeddingError("embedding_timeout")),
        lambda: 1,
        lambda: NOW,
    )
    assert (result.outcome, result.job.job_status, result.parse_status) == (
        INDEX_FAILED,
        JOB_PENDING,
        PARSE_UPLOADED,
    )
    assert result.job.error_message == "embedding_timeout"


def test_invalid_input_is_not_retried() -> None:
    job, rag_file = _running()
    result = index_file(
        job,
        rag_file,
        LAW_TEXT,
        FakeEmbedder(EmbeddingError("embedding_invalid_input")),
        lambda: 1,
        lambda: NOW,
    )
    assert (result.job.job_status, result.parse_status) == (JOB_FAILED, PARSE_FAILED)


def test_retry_stops_after_max_attempts() -> None:
    job = RagJob("job-1", "f-1", job_status=JOB_RUNNING, attempts=indexing.MAX_ATTEMPTS)
    failed, parse_status = record_failure(job, "embedding_quota_exceeded", NOW)
    assert (failed.job_status, parse_status) == (JOB_FAILED, PARSE_FAILED)
    assert is_retryable("embedding_unreachable")
    assert not is_retryable(ERROR_EMPTY_TEXT)


def test_index_file_requires_started_job() -> None:
    with pytest.raises(RagRuleError):
        index_file(
            RagJob("job-1", "f-1"),
            RagFile("f-1", "kb-1", "x"),
            LAW_TEXT,
            FakeEmbedder(),
            lambda: 1,
            lambda: NOW,
        )


def test_chunks_are_prefixed_with_title_chapter_and_section() -> None:
    from app.services.rag_indexing_service import prepare_chunks

    text = (
        "第一章 總則\n第 1 條 本法定義。\n"
        "第 四 章 稅額計算\n第 一 節 一般稅額計算\n第 14 條 營業人銷售貨物。\n"
        "第 二 節 特種稅額計算\n第 21 條 特種營業人。"
    )

    contents = [d.content for d in prepare_chunks(text, size=30, overlap=5, title="營業稅法")]

    assert contents[0].startswith("《營業稅法》第一章總則\n")
    assert contents[1].startswith("《營業稅法》第四章稅額計算／第一節一般稅額計算\n")
    assert contents[2].startswith("《營業稅法》第四章稅額計算／第二節特種稅額計算\n")
