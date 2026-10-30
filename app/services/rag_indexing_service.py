"""
模組名稱：app.services.rag_indexing_service
功能說明：RAG 索引規則——文字擷取（可注入）、中文切段、以 VertexEmbeddingClient 取得向量、寫入前版本核對與重試規則。
相依模組：app.schemas.rag、app.integrations.vertex_embedding、app.integrations.document_ai
注意事項：
    - 依 docs/RAG_DEVELOPMENT.md：TXT／MD 由程式解碼；PDF 使用既有 DocumentAIClient（掃描檔也能辨識），
      單次最多 15 頁，超過標為失敗（rag_pdf_page_limit）；DOCX 尚未實作，標為失敗
      （rag_unsupported_format）。擷取不到文字一律失敗，不可標為 ready。
    - 向量直接使用共用模組 VertexEmbeddingClient.embed_texts()（task="document"），用戶端由呼叫端注入；
      測試以 RAG_DEVELOPMENT.md 的 FakeEmbedding 替身代替，不呼叫 Google。
    - 本模組不存取資料庫；DB 交易與「寫入前鎖定 rag_files 核對 file_version」由 rag_admin_service 處理，
      呼叫模型期間不持有 DB 交易。
    - ADMIN 每次請求上限 30 秒：一次只處理一份文件，向量分批呼叫並共用時間預算。
"""

import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import PurePosixPath
from typing import Protocol

from app.integrations.document_ai import DocumentAIError, OcrResult
from app.integrations.vertex_embedding import EmbeddingError, VertexEmbeddingClient
from app.schemas.rag import (
    ERROR_EMPTY_TEXT,
    ERROR_PDF_PAGE_LIMIT,
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
    ChunkDraft,
    ExtractedPage,
    ExtractedText,
    IndexedChunk,
    IndexResult,
    RagFile,
    RagJob,
    RagRuleError,
)

# 切段起始值（docs/RAG_DEVELOPMENT.md：約 800 字、重疊 100 字，依實測調整）
DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100
# 每次呼叫向量模組的段數；模組內部再以最多 8 條併發處理
EMBED_BATCH_SIZE = 32
# 一次索引的向量時間預算（ADMIN 函式上限 30 秒，保留讀檔與寫入時間）
INDEX_TIME_BUDGET_SECONDS = 20.0
# 可重試的錯誤最多執行次數
MAX_ATTEMPTS = 3

# 可稍後重試的錯誤（docs/RAG_DEVELOPMENT.md 錯誤代碼表「工作保留，稍後重試」）
RETRYABLE_CODES = frozenset(
    {
        "embedding_quota_exceeded",
        "embedding_timeout",
        "embedding_unreachable",
        "embedding_failed",
        "rag_storage_read_failed",
        "document_ai_quota_exceeded",
        "document_ai_timeout",
        "document_ai_unreachable",
        "document_ai_failed",
    }
)

# 目前可直接讀取的文字格式
PLAIN_TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown"})
PLAIN_TEXT_MIME_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})
PDF_MIME_TYPE = "application/pdf"
# PDF 辨識的時間上限（實測 15 頁約 14 秒；ADMIN 函式上限 30 秒，其餘留給向量與寫入）
PDF_TIMEOUT_SECONDS = 15.0
# Document AI 超過單次頁數上限時 detail 內含的字樣
_PAGE_LIMIT_DETAIL = "PAGE_LIMIT_EXCEEDED"

# 段落起點：「第 X 條／點／章／節」、「第 X 條之 Y」、「一、」、「附件一」；遇到時另起一段，盡量不切斷條文
_NUMERAL = r"[〇零一二三四五六七八九十百千兩\d]+"
_BOUNDARY = re.compile(
    rf"^\s*(?:第\s*{_NUMERAL}\s*(?:條(?:之\s*{_NUMERAL})?|點|章|節)"
    rf"|{_NUMERAL}、|附件\s*{_NUMERAL})"
)
# 長條文需要切開時，優先在句末切
_SENTENCE_END = "。；！？\n"


class TextExtractionError(Exception):
    """文字擷取失敗；code 為固定代碼，不含文件內容。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TextExtractor(Protocol):
    """文字擷取介面；預設實作為 DocumentTextExtractor，測試注入替身。"""

    def extract(self, content: bytes, *, filename: str, mime_type: str | None) -> ExtractedText:
        """把檔案內容轉為依頁排列的文字。"""
        ...


class PlainTextExtractor:
    """txt／md 文字擷取：以 UTF-8（可含 BOM）解碼；其他格式拋出 rag_unsupported_format。"""

    def extract(self, content: bytes, *, filename: str, mime_type: str | None) -> ExtractedText:
        """讀取純文字檔。

        Args:
            content: 檔案內容。
            filename: 原始檔名，用副檔名判斷格式。
            mime_type: rag_files.mime_type。

        Returns:
            單頁文字。

        Raises:
            TextExtractionError: 不支援的格式，或不是 UTF-8。
        """
        suffix = PurePosixPath(filename).suffix.lower()
        media = (mime_type or "").split(";", 1)[0].strip().lower()
        if suffix not in PLAIN_TEXT_SUFFIXES and media not in PLAIN_TEXT_MIME_TYPES:
            raise TextExtractionError(ERROR_UNSUPPORTED_FORMAT)
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise TextExtractionError(ERROR_TEXT_DECODE) from None
        return ExtractedText((ExtractedPage(1, text),))


class PdfProcessor(Protocol):
    """DocumentAIClient.process 的最小介面，測試以替身注入。"""

    def process(
        self, content: bytes, mime_type: str, *, timeout_seconds: float | None = None
    ) -> OcrResult:
        """辨識文件並回傳文字與頁面。"""
        ...


def _is_pdf(filename: str, mime_type: str | None) -> bool:
    media = (mime_type or "").split(";", 1)[0].strip().lower()
    return media == PDF_MIME_TYPE or PurePosixPath(filename).suffix.lower() == ".pdf"


class DocumentTextExtractor:
    """依格式擷取文字：TXT／MD 交給 PlainTextExtractor，PDF 交給 Document AI，其他格式失敗。

    Document AI 用戶端以工廠延後建立，只有遇到 PDF 才建立；同一個擷取器只建立一次，
    由呼叫端在工作結束後呼叫 close()。
    """

    def __init__(
        self,
        pdf_client_factory: Callable[[], PdfProcessor],
        *,
        timeout_seconds: float = PDF_TIMEOUT_SECONDS,
    ) -> None:
        self._factory = pdf_client_factory
        self._client: PdfProcessor | None = None
        self._timeout_seconds = timeout_seconds
        self._plain = PlainTextExtractor()

    def extract(self, content: bytes, *, filename: str, mime_type: str | None) -> ExtractedText:
        """擷取文字。

        Args:
            content: 檔案內容。
            filename: 原始檔名。
            mime_type: rag_files.mime_type。

        Returns:
            擷取出的文字（可能為空白，由切段判斷為 rag_empty_text）。

        Raises:
            TextExtractionError: 不支援的格式、不是 UTF-8、PDF 超過 15 頁或 Document AI 錯誤
                （代碼為 document_ai_*，不含文件內容）。
        """
        if not _is_pdf(filename, mime_type):
            return self._plain.extract(content, filename=filename, mime_type=mime_type)
        try:
            if self._client is None:
                self._client = self._factory()
            result = self._client.process(
                content, PDF_MIME_TYPE, timeout_seconds=self._timeout_seconds
            )
        except DocumentAIError as error:
            if error.code == "document_ai_invalid_input" and _PAGE_LIMIT_DETAIL in error.detail:
                raise TextExtractionError(ERROR_PDF_PAGE_LIMIT) from None
            raise TextExtractionError(error.code) from None
        return ExtractedText((ExtractedPage(1, result.text or ""),))

    def close(self) -> None:
        """關閉已建立的 Document AI 用戶端。"""
        close = getattr(self._client, "close", None)
        if callable(close):
            close()
        self._client = None


# ---- 切段 ----


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _split_units(text: str) -> list[str]:
    """依空行與條文起點切成段落單位，單位內保留原本換行。"""
    units: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if not line.strip():
            if current:
                units.append("\n".join(current))
                current = []
            continue
        if _BOUNDARY.match(line) and current:
            units.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        units.append("\n".join(current))
    return units


def _split_long(unit: str, size: int, overlap: int) -> list[str]:
    """把超過 size 的單位切開：優先在後半段的句末切，相鄰兩段重疊 overlap 字。"""
    pieces: list[str] = []
    start = 0
    while start < len(unit):
        end = min(start + size, len(unit))
        if end < len(unit):
            window = unit[start + size // 2 : end]
            cut = max(window.rfind(mark) for mark in _SENTENCE_END)
            if cut >= 0:
                end = start + size // 2 + cut + 1
        piece = unit[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(unit):
            break
        start = max(end - overlap, start + 1)
    return pieces


def split_into_chunks(
    text: str, size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[str]:
    """把文件文字切成段落，供向量化。

    先依空行與條文起點（第 X 條／點／章／節、一、、附件一）分成單位，再把相鄰單位
    合併到不超過 size 字；條文起點一律另起單位，所以不會從條文中間開始一段。
    單一單位超過 size 時才在句末切開，並與前一段重疊 overlap 字，避免語意斷裂。
    以字元計算（中文一字一個字元），不估算 token。

    Args:
        text: 文件文字。
        size: 每段最多字元數。
        overlap: 長單位切開時相鄰兩段的重疊字元數，須小於 size。

    Returns:
        段落清單；空字串或只有空白時為空清單。

    Raises:
        ValueError: size 不是正數，或 overlap 不在 0～size-1。
    """
    if size <= 0:
        raise ValueError("size 必須大於 0")
    if not 0 <= overlap < size:
        raise ValueError("overlap 必須介於 0 與 size-1 之間")
    normalized = _normalize(text)
    if not normalized:
        return []
    chunks: list[str] = []
    current = ""
    for unit in _split_units(normalized):
        if current and len(current) + 1 + len(unit) <= size:
            current = f"{current}\n{unit}"
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(unit) <= size:
            current = unit
        else:
            *full, last = _split_long(unit, size, overlap)
            chunks.extend(full)
            current = last
    if current:
        chunks.append(current)
    return chunks


def prepare_chunks(
    text: str, size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP
) -> tuple[ChunkDraft, ...]:
    """產生待 embedding 的段落資料（chunk_index 從 0 開始）。

    Args:
        text: 文件文字。
        size: 每段最多字元數。
        overlap: 長段重疊字元數。

    Returns:
        待 embedding 的段落。
    """
    return tuple(
        ChunkDraft(index, content, len(content))
        for index, content in enumerate(split_into_chunks(text, size, overlap))
    )


# ---- 工作與重試 ----


def is_retryable(code: str) -> bool:
    """此錯誤是否可稍後重試。

    Args:
        code: 固定錯誤代碼。

    Returns:
        配額、逾時、連線、服務暫時錯誤、讀檔失敗為 True；格式、輸入、設定、權限、空文字等為 False。
    """
    return code in RETRYABLE_CODES


def start_job(job: RagJob, rag_file: RagFile, now: datetime) -> tuple[RagJob, RagFile]:
    """開始處理一個索引工作：記下文件版本、次數 +1、文件改為索引中。

    此結果寫入並提交後才呼叫模型（呼叫模型期間不持有 DB 交易）。

    Args:
        job: 等待中的工作。
        rag_file: 工作對應的文件。
        now: 開始時間。

    Returns:
        (執行中的工作, 索引中的文件)。

    Raises:
        RagRuleError: 工作不在等待中，或與文件不符。
    """
    if job.job_status != JOB_PENDING:
        raise RagRuleError("JOB_NOT_PENDING", "只有等待中的工作可以開始")
    if job.rag_file_id != rag_file.rag_file_id:
        raise RagRuleError("JOB_FILE_MISMATCH", "工作與文件不符")
    running = replace(
        job,
        job_status=JOB_RUNNING,
        attempts=job.attempts + 1,
        started_at=now,
        finished_at=None,
        error_message=None,
        file_version=rag_file.file_version,
    )
    return running, replace(rag_file, parse_status=PARSE_PROCESSING)


def record_failure(job: RagJob, code: str, now: datetime) -> tuple[RagJob, str]:
    """記錄失敗：可重試且未達次數上限時回到等待，否則標為失敗。

    Args:
        job: 執行中的工作。
        code: 固定錯誤代碼（不可含文件內容）。
        now: 結束時間。

    Returns:
        (更新後的工作, 要寫回 rag_files 的 parse_status)；等待重試時文件回到 uploaded，
        最終失敗時為 failed（掃描檔、空文字、不支援格式等不可標為 ready）。
    """
    if is_retryable(code) and job.attempts < MAX_ATTEMPTS:
        return replace(job, job_status=JOB_PENDING, error_message=code, finished_at=now), (
            PARSE_UPLOADED
        )
    return replace(job, job_status=JOB_FAILED, error_message=code, finished_at=now), PARSE_FAILED


def failed_result(job: RagJob, code: str, now: datetime) -> IndexResult:
    """依重試規則產生失敗結果。

    Args:
        job: 執行中的工作。
        code: 固定錯誤代碼。
        now: 結束時間。

    Returns:
        outcome 為 failed 的索引結果。
    """
    updated, parse_status = record_failure(job, code, now)
    return IndexResult(INDEX_FAILED, updated, parse_status=parse_status, error_code=code)


def embed_chunks(
    job: RagJob,
    rag_file: RagFile,
    text: str,
    client: VertexEmbeddingClient,
    now: Callable[[], datetime],
    *,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    time_budget_seconds: float = INDEX_TIME_BUDGET_SECONDS,
) -> IndexResult:
    """切段並以 VertexEmbeddingClient 取得向量（尚未核對版本，不寫入 DB）。

    向量一筆失敗即整批失敗，依 record_failure 規則決定是否重試。

    Args:
        job: start_job 後執行中的工作（含開始時的 file_version）。
        rag_file: 文件。
        text: 擷取出的文字。
        client: 向量用戶端（同一工作共用一個，權杖才能重用）。
        now: 取得目前時間。
        size: 每段最多字元數。
        overlap: 長段重疊字元數。
        time_budget_seconds: 向量呼叫的總時間預算。

    Returns:
        成功時 outcome 為 ready、chunks 為段落與向量（job 仍為執行中，待 confirm_version）；失敗時為 failed。

    Raises:
        RagRuleError: 工作不是執行中或沒有記下文件版本。
    """
    if job.job_status != JOB_RUNNING or job.file_version is None:
        raise RagRuleError("JOB_NOT_RUNNING", "請先開始工作並記下文件版本")
    drafts = prepare_chunks(text, size, overlap)
    if not drafts:
        return failed_result(job, ERROR_EMPTY_TEXT, now())

    deadline = time.monotonic() + time_budget_seconds
    indexed: list[IndexedChunk] = []
    model: str | None = None
    for start in range(0, len(drafts), EMBED_BATCH_SIZE):
        batch = drafts[start : start + EMBED_BATCH_SIZE]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return failed_result(job, "embedding_timeout", now())
        try:
            result = client.embed_texts(
                [d.content for d in batch],
                task="document",
                title=rag_file.title,
                timeout_seconds=remaining,
            )
        except EmbeddingError as error:
            return failed_result(job, error.code, now())
        if len(result.vectors) != len(batch) or (model is not None and result.model != model):
            return failed_result(job, "embedding_failed", now())
        model = result.model
        indexed.extend(
            IndexedChunk(d.chunk_index, d.content, tuple(v), result.model)
            for d, v in zip(batch, result.vectors, strict=True)
        )
    return IndexResult(INDEX_READY, job, chunks=tuple(indexed), parse_status=PARSE_READY)


def confirm_version(result: IndexResult, current_version: int, now: datetime) -> IndexResult:
    """寫入前核對文件版本：相同才維持 ready，不同就丟棄段落。

    Args:
        result: embed_chunks 的 ready 結果。
        current_version: 寫入交易中鎖定 rag_files 後讀到的 file_version。
        now: 結束時間。

    Returns:
        版本相同：工作 succeeded、可寫入；不同：outcome 為 stale、沒有段落、不更動文件狀態。
    """
    if result.outcome != INDEX_READY:
        return result
    if current_version != result.job.file_version:
        stale = replace(
            result.job, job_status=JOB_FAILED, error_message=ERROR_VERSION_MISMATCH, finished_at=now
        )
        return IndexResult(INDEX_STALE, stale, error_code=ERROR_VERSION_MISMATCH)
    done = replace(result.job, job_status=JOB_SUCCEEDED, error_message=None, finished_at=now)
    return replace(result, job=done)


def index_file(
    job: RagJob,
    rag_file: RagFile,
    text: str,
    client: VertexEmbeddingClient,
    current_file_version: Callable[[], int],
    now: Callable[[], datetime],
    *,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    time_budget_seconds: float = INDEX_TIME_BUDGET_SECONDS,
) -> IndexResult:
    """切段、取得向量，再呼叫 current_file_version() 核對版本（embed_chunks＋confirm_version）。

    Args:
        job: start_job 後執行中的工作。
        rag_file: 文件。
        text: 擷取出的文字。
        client: 向量用戶端。
        current_file_version: 寫入前讀取目前文件版本（正式版在寫入交易中鎖定 rag_files 後讀取）。
        now: 取得目前時間。
        size: 每段最多字元數。
        overlap: 長段重疊字元數。
        time_budget_seconds: 向量呼叫的總時間預算。

    Returns:
        索引結果；ready 時 chunks 為要在同一交易「刪舊、寫新、設 ready」的段落。

    Raises:
        RagRuleError: 工作不是執行中或沒有記下文件版本。
    """
    result = embed_chunks(
        job,
        rag_file,
        text,
        client,
        now,
        size=size,
        overlap=overlap,
        time_budget_seconds=time_budget_seconds,
    )
    if result.outcome != INDEX_READY:
        return result
    return confirm_version(result, current_file_version(), now())
