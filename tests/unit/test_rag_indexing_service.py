"""
模組名稱：tests.unit.test_rag_indexing_service
功能說明：RAG 索引測試——中文切段與條文邊界、txt／md 擷取與不支援格式、版本核對、掃描檔不可 ready、重試規則。
注意事項：
    - 只用自己寫的短文字 fixture，不使用真實法規檔案。
    - 向量以確定性的假用戶端（同 docs/RAG_DEVELOPMENT.md 的 FakeEmbedding）替代，不呼叫 Google。
"""

import random
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.integrations.document_ai import OcrLine, OcrPage, OcrResult, TextSegment
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
    DocumentTextExtractor,
    PlainTextExtractor,
    TextExtractionError,
    index_file,
    is_retryable,
    prepare_chunks,
    record_failure,
    restore_item_numbers,
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
    rag_file = RagFile("f-1", "作業要點", file_version=version)
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


# ---- 項次還原 ----


MARGIN = 0.09
BODY = 0.13


def _ocr(rows: Sequence[tuple[float, float, float, str]]) -> OcrResult:
    """依閱讀順序組成辨識結果；每列為（左緣, 右緣, 上緣, 文字），行高固定 0.01。"""
    text = ""
    lines = []
    for left, right, top, content in rows:
        start = len(text)
        text += f"{content}\n"
        box = ((left, top), (right, top), (right, top + 0.01), (left, top + 0.01))
        lines.append(OcrLine(content, None, box, (TextSegment(start, len(text)),)))
    return OcrResult(text, (OcrPage(1, 1000.0, 1400.0, None, tuple(lines)),), {})


# 左側數字被 OCR 擠到同一段：閱讀順序與實際位置不同，只有座標能對回正確的行
ARTICLE_ROWS = (
    (BODY, 0.20, 0.10, "第32條"),
    (MARGIN, 0.90, 0.12, "1 營業人銷售貨物或勞務應開立統一發票交付買"),
    (MARGIN, 0.10, 0.16, "2"),
    (MARGIN, 0.10, 0.18, "3"),
    (BODY, 0.80, 0.14, "受人，但小規模營業人得掣發普通收據免用發票。"),
    (BODY, 0.50, 0.16, "營業人對於應稅貨物或勞務之定價應內含營業稅。"),
    (BODY, 0.60, 0.18, "統一發票之格式與使用辦法由財政部定之。"),
)


def test_item_numbers_follow_coordinates_not_reading_order() -> None:
    assert restore_item_numbers(_ocr(ARTICLE_ROWS)).split("\n") == [
        "第32條",
        "〔第32條第1項〕 營業人銷售貨物或勞務應開立統一發票交付買",
        "受人，但小規模營業人得掣發普通收據免用發票。",
        "〔第32條第2項〕 營業人對於應稅貨物或勞務之定價應內含營業稅。",
        "〔第32條第3項〕 統一發票之格式與使用辦法由財政部定之。",
        "",
    ]


def test_item_numbers_reset_for_each_article() -> None:
    rows = (
        *ARTICLE_ROWS,
        (BODY, 0.22, 0.20, "第32-1條"),
        (BODY, 0.90, 0.22, "營業人以網際網路開立電子發票者應依規定傳輸。"),
        (MARGIN, 0.10, 0.22, "1"),
        (MARGIN, 0.60, 0.24, "2 前項所稱載具指下列號碼。"),
    )
    lines = restore_item_numbers(_ocr(rows)).split("\n")
    assert lines[5:8] == [
        "第32-1條",
        "〔第32-1條第1項〕 營業人以網際網路開立電子發票者應依規定傳輸。",
        "〔第32-1條第2項〕 前項所稱載具指下列號碼。",
    ]


def test_wrapped_line_starting_with_digit_is_not_an_item() -> None:
    rows = (
        *ARTICLE_ROWS,
        (BODY, 0.60, 0.20, "5 年內不得再申請核定之營業人應依規定辦理。"),
    )
    assert "5 年內不得再申請核定之營業人應依規定辦理。" in restore_item_numbers(_ocr(rows))


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param(
            (
                (BODY, 0.20, 0.10, "第5條"),
                (MARGIN, 0.60, 0.12, "1 營業人應依規定開立統一發票。"),
                (MARGIN, 0.10, 0.14, "3"),
                (BODY, 0.60, 0.14, "前項統一發票應保存五年以上。"),
            ),
            id="not-consecutive",
        ),
        pytest.param(
            (
                (BODY, 0.20, 0.10, "第5條"),
                (MARGIN, 0.60, 0.12, "1 營業人應依規定開立統一發票。"),
                (BODY, 0.60, 0.14, "前項統一發票應保存五年以上。"),
            ),
            id="only-one-number",
        ),
        pytest.param(
            (
                (BODY, 0.60, 0.10, "第一類貨物應依規定開立統一發票。"),
                (MARGIN, 0.10, 0.12, "1"),
                (BODY, 0.60, 0.12, "買賣業銷售貨物之營業應開立發票。"),
                (MARGIN, 0.10, 0.14, "2"),
                (BODY, 0.60, 0.14, "製造業使用原料生產應開立發票。"),
            ),
            id="outside-article",
        ),
    ],
)
def test_unreliable_item_numbers_are_not_labelled(
    rows: Sequence[tuple[float, float, float, str]],
) -> None:
    restored = restore_item_numbers(_ocr(rows))
    assert "〔" not in restored
    assert not any(line.strip().isdigit() for line in restored.split("\n"))


def test_number_without_body_on_same_row_stays_in_text() -> None:
    rows = (*ARTICLE_ROWS, (MARGIN, 0.10, 0.95, "7"))
    assert restore_item_numbers(_ocr(rows)).endswith("\n7\n")


def test_text_without_coordinates_is_unchanged() -> None:
    text = "第1條\n1\n營業人應依規定開立統一發票。\n"
    assert restore_item_numbers(OcrResult(text, (), {})) == text


def test_pdf_extraction_labels_items() -> None:
    class Pdf:
        def process(
            self, content: bytes, mime_type: str, *, timeout_seconds: float | None = None
        ) -> OcrResult:
            return _ocr(ARTICLE_ROWS)

    extractor = DocumentTextExtractor(Pdf)
    extracted = extractor.extract(b"%PDF-1.7", filename="法規.pdf", mime_type=None)
    assert "〔第32條第2項〕 營業人對於應稅貨物" in extracted.text


LONG_ITEM = "營業人應依規定開立統一發票並妥善保存相關憑證。" * 60


def test_continued_chunk_keeps_article_and_item_identity() -> None:
    text = f"第32條\n〔第32條第1項〕 甲\n〔第32條第2項〕 {LONG_ITEM}\n第33條\n乙"
    contents = [draft.content for draft in prepare_chunks(text, title="測試法")]
    continued = [content for content in contents if "（接續" in content]
    assert continued
    assert all(content.startswith("《測試法》（接續第32條第2項）\n") for content in continued)
    assert not any(
        "（接續" in content for content in contents if "第33條" in content.split("\n")[1]
    )


def test_continued_chunk_without_items_keeps_article_only() -> None:
    contents = [draft.content for draft in prepare_chunks(f"第7條\n{LONG_ITEM}", title="測試法")]
    assert any(content.startswith("《測試法》（接續第7條）\n") for content in contents[1:])


def test_article_cited_in_body_is_not_taken_as_identity() -> None:
    text = f"依第九十九條規定辦理。\n{LONG_ITEM}"
    contents = [draft.content for draft in prepare_chunks(text, title="測試法")]
    assert not any("接續" in content for content in contents)


def test_chunk_starting_at_item_label_needs_no_continuation() -> None:
    text = f"第32條\n〔第32條第1項〕 {LONG_ITEM}\n〔第32條第2項〕 甲"
    contents = [draft.content for draft in prepare_chunks(text, title="測試法")]
    assert not any(
        "（接續" in content and content.split("\n")[1].startswith("〔第32條第2項〕")
        for content in contents
    )


def test_quoted_article_in_body_is_not_taken_as_identity() -> None:
    body = "本條引用其他規定如下：\n第99條第1項 規定之資料。"
    text = f"第32條\n{body}\n{LONG_ITEM}"
    contents = [draft.content for draft in prepare_chunks(text, title="測試法")]
    assert not any("第99條" in content.split("\n")[0] for content in contents)
    assert any("（接續第32條）" in content.split("\n")[0] for content in contents)


def test_overlapped_chunk_before_its_item_label_stays_unknown() -> None:
    chunk = "前一項結尾。\n〔第32條第2項〕 後一項開頭"
    labelled = indexing.add_heading_context(
        ["第32條\n〔第32條第1項〕 甲", "乙\n〔第32條第2項〕 丙", chunk]
    )
    assert labelled[1].startswith("（接續第32條第1項）")
    assert "（接續" not in labelled[2]


def test_dash_article_starts_a_new_chunk_unit() -> None:
    raw = "第32條\n" + "甲" * 730 + "\n第32-1條\n" + "乙" * 30 + "。\n" + "乙" * 1000
    contents = [draft.content for draft in prepare_chunks(raw, title="合成文件")]
    assert contents[0].split("\n")[1] == "第32條" and "第32-1條" not in contents[0]
    assert contents[1].split("\n")[1] == "第32-1條"
    assert all("接續第32條）" not in content for content in contents)
    assert all(content.startswith("《合成文件》（接續第32-1條）") for content in contents[2:])


def test_overlap_keeps_identity_of_where_the_chunk_starts() -> None:
    head = "第32條\n〔第32條第1項〕 " + "甲" * 40 + "。"
    tail = "甲" * 10 + "。\n〔第32條第2項〕 " + "乙" * 40 + "。"
    labelled = indexing.add_heading_context([f"{head}{tail}", f"{tail}\n乙乙乙。"])
    assert labelled[1].startswith("（接續第32條第1項）")


def test_chunk_after_overlap_uses_identity_from_its_own_start() -> None:
    first = "第32條\n〔第32條第1項〕 甲甲甲。\n〔第32條第2項〕 乙乙乙。"
    second = "乙乙乙。\n丙丙丙。"
    third = "丁丁丁。"
    labelled = indexing.add_heading_context([first, second, third])
    assert labelled[1].startswith("（接續第32條第2項）")
    assert labelled[2].startswith("（接續第32條第2項）")


def test_overlap_never_cuts_through_an_item_label() -> None:
    head = "第32條\n〔第32條第1項〕 "
    text = head + "甲" * (599 - len(head)) + "\n"
    text += "〔第32條第2項〕 " + "乙" * 95 + "。" + "乙" * 1000
    contents = [draft.content for draft in prepare_chunks(text, title="合成文件")]
    assert contents[1].split("\n")[1].startswith("〔第32條第2項〕 ")
    assert all(content.startswith("《合成文件》（接續第32條第2項）") for content in contents[2:])


def _random_document(rng: random.Random) -> tuple[str, dict[str, str]]:
    """每一項用不同的字，就能從段落開頭的字反查真實條項。"""
    owner: dict[str, str] = {}
    code = 0x6000
    lines: list[str] = []
    for number in range(1, rng.randint(2, 6)):
        article = f"第{number}條" if rng.random() < 0.7 else f"第{number}-1條"
        lines.append(article)
        item_count = rng.choice([0, 2, 3, 4])
        for item in range(1, max(item_count, 1) + 1):
            code += 1
            char = chr(code)
            owner[char] = f"{article}第{item}項" if item_count else article
            body = "".join(
                char * rng.randint(1, 420) + rng.choice(["。", "；", ""])
                for _ in range(rng.randint(1, 5))
            )
            lines.append(f"〔{article}第{item}項〕 {body}" if item_count else body)
    return "\n".join(lines), owner


@pytest.mark.parametrize("seed", range(300))
def test_random_documents_keep_true_identity_at_every_cut(seed: int) -> None:
    text, owner = _random_document(random.Random(seed))
    for draft in prepare_chunks(text, title="測試"):
        prefix, first, *_ = draft.content.split("\n")
        if first.startswith("〔") or first.startswith("第"):
            assert "（接續" not in prefix
        else:
            assert "項〕" not in first[:12]
            # 段落可能從上一句的標點開始，取第一個屬於某項的字
            start = next((char for char in first if char in owner), None)
            if start is not None:
                assert prefix == f"《測試》（接續{owner[start]}）"


def test_long_first_item_does_not_leave_heading_only_chunk() -> None:
    text = f"第32條\n〔第32條第1項〕 {LONG_ITEM}"
    contents = [draft.content for draft in prepare_chunks(text, title="測試法")]
    assert len(contents[0].split("\n")) > 2


def test_chapter_change_clears_article_for_item_numbers() -> None:
    rows = (
        *ARTICLE_ROWS,
        (BODY, 0.40, 0.20, "第二章 總則"),
        (MARGIN, 0.60, 0.22, "1 營業人應依規定開立統一發票。"),
        (MARGIN, 0.60, 0.24, "2 營業人應妥善保存相關憑證。"),
    )
    restored = restore_item_numbers(_ocr(rows))
    assert "〔第32條第3項〕" in restored
    assert "營業人應依規定開立統一發票。" in restored
    assert restored.count("〔") == 3


def test_reprinted_article_heading_keeps_item_sequence() -> None:
    rows = (
        *ARTICLE_ROWS,
        (BODY, 0.20, 0.20, "第32條"),
        (MARGIN, 0.60, 0.22, "4 營業人應依規定開立統一發票。"),
        (MARGIN, 0.60, 0.24, "5 營業人應妥善保存相關憑證。"),
    )
    restored = restore_item_numbers(_ocr(rows))
    assert "〔第32條第4項〕" in restored and "〔第32條第5項〕" in restored


def test_original_text_starting_with_item_words_is_kept() -> None:
    rows = (
        (BODY, 0.20, 0.10, "第5條"),
        (BODY, 0.60, 0.12, "第1項 所稱營業人應依規定開立統一發票。"),
    )
    assert "第1項 所稱營業人" in restore_item_numbers(_ocr(rows))


def test_new_chapter_clears_article_identity() -> None:
    text = f"第7條\n甲\n第二章 總則\n{LONG_ITEM}"
    contents = [draft.content for draft in prepare_chunks(text, title="測試法")]
    assert not any("第7條" in content.split("\n")[0] for content in contents)


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
            RagFile("f-1", "x"),
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
