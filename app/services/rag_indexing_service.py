"""
模組名稱：app.services.rag_indexing_service
功能說明：RAG 索引規則——文字擷取（可注入）、中文切段、以 VertexEmbeddingClient 取得向量、寫入前版本核對與重試規則。
相依模組：app.schemas.rag、app.integrations.vertex_embedding、app.integrations.document_ai
注意事項：
    - 依 docs/RAG_DEVELOPMENT.md：TXT／MD 由程式解碼；PDF 使用既有 DocumentAIClient（掃描檔也能辨識），
      單次最多 15 頁，超過標為失敗（rag_pdf_page_limit）；DOCX 尚未實作，標為失敗
      （rag_unsupported_format）。擷取不到文字一律失敗，不可標為 ready。
    - PDF 左側印的項次依座標併回內文（restore_item_numbers），改了這段規則要重新建立索引才生效。
    - 向量直接使用共用模組 VertexEmbeddingClient.embed_texts()（task="document"），用戶端由呼叫端注入；
      測試以 RAG_DEVELOPMENT.md 的 FakeEmbedding 替身代替，不呼叫 Google。
    - 本模組不存取資料庫；DB 交易與「寫入前鎖定 rag_files 核對 file_version」由 rag_admin_service 處理，
      呼叫模型期間不持有 DB 交易。
    - ADMIN 每次請求上限 60 秒：一次索引沿用較短預算；一次只處理一份文件，向量分批呼叫並共用時間預算。
"""

import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import PurePosixPath
from statistics import median
from typing import Protocol

from app.integrations.document_ai import DocumentAIError, OcrLine, OcrPage, OcrResult
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
# 一次索引的向量時間預算（ADMIN 函式上限 60 秒，索引沿用較短預算）
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
# PDF 辨識的時間上限（實測 15 頁約 14 秒；索引預算 25 秒，其餘留給向量與寫入）
PDF_TIMEOUT_SECONDS = 15.0
# Document AI 超過單次頁數上限時 detail 內含的字樣
_PAGE_LIMIT_DETAIL = "PAGE_LIMIT_EXCEEDED"

# 段落起點：「第 X 條／點／章／節」、「第 X 條之 Y」、「第 X-Y 條」、「一、」、「附件一」；遇到時另起一段，盡量不切斷條文
_NUMERAL = r"[〇零一二三四五六七八九十百千兩\d]+"
_BOUNDARY = re.compile(
    rf"^\s*(?:第\s*{_NUMERAL}(?:\s*-\s*\d+)?\s*(?:條(?:之\s*{_NUMERAL})?|點|章|節)"
    rf"|{_NUMERAL}、|附件\s*{_NUMERAL})"
)
# 章、節標題（例：第 四 章 稅額計算、第一節 一般稅額計算），用來在每段前面標示所屬章節
_CHAPTER = re.compile(rf"^\s*第\s*{_NUMERAL}\s*章")
_SECTION = re.compile(rf"^\s*第\s*{_NUMERAL}\s*節")
# 章節標題最多保留的字數（避免把整行內文當標題）
_HEADING_MAX_CHARS = 30
# 長條文需要切開時，優先在句末切
_SENTENCE_END = "。；！？\n"
# 法規 PDF 把項次印在內文左側：單獨成行的數字，或併在該項第一行開頭（例：2 前項所稱載具）
_MARGIN_NUMBER = re.compile(r"\s*(\d{1,2})\s*")
_INLINE_NUMBER = re.compile(r"\s*(\d{1,2})\s+(?=\S)")
# 程式補上的項次用六角括號，和原文引用的「第99條第1項」分開；通過檢查後才帶上所屬條號
_ITEM_LABEL = re.compile(r"〔第(\d{1,2})項〕 ")
_ITEM_IDENTITY = re.compile(r"〔(第\S*?[條點]\S*?)第(\d{1,2})項〕 ")
# 條、點標題獨立成行（例：第32條、第32-1條），用來分組檢查項次
_ARTICLE = re.compile(rf"\s*第\s*{_NUMERAL}(?:\s*-\s*\d+)?\s*(?:條(?:之\s*{_NUMERAL})?|點)\s*")
# 用來估算內文左緣的最短行長，排除條號等短行
_BODY_LINE_MIN_CHARS = 10
# 併在行首的項次須比內文左緣更靠左（頁寬比例）；內文換行後開頭剛好是數字時不算項次
_MARGIN_GAP = 0.02


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


def _extent(line: OcrLine, axis: int) -> tuple[float, float]:
    values = [point[axis] for point in line.bounding_box]
    return min(values), max(values)


def _same_row(number: OcrLine, lines: list[OcrLine]) -> OcrLine | None:
    """找出與左側項次同一列、位於其右邊的內文行；列重疊不到一半視為沒有。"""
    top, bottom = _extent(number, 1)
    best = None
    best_overlap = (bottom - top) / 2
    for line in lines:
        if _MARGIN_NUMBER.fullmatch(line.text) or _extent(line, 0)[0] < _extent(number, 0)[1]:
            continue
        line_top, line_bottom = _extent(line, 1)
        overlap = min(bottom, line_bottom) - max(top, line_top)
        if overlap > best_overlap:
            best, best_overlap = line, overlap
    return best


def _page_item_edits(page: OcrPage, text: str) -> list[tuple[int, int, str]]:
    """依座標把本頁左側項次改為文字位置的修改（開始、結束、取代文字）。"""
    lines = [line for line in page.lines if line.bounding_box and len(line.text_segments) == 1]
    lefts = [
        _extent(line, 0)[0]
        for line in lines
        if len(line.text.strip()) >= _BODY_LINE_MIN_CHARS and not _INLINE_NUMBER.match(line.text)
    ]
    if not lefts:
        return []
    body_left = median(lefts)
    edits: list[tuple[int, int, str]] = []
    for line in lines:
        start, end = line.text_segments[0].start, line.text_segments[0].end
        source = text[start:end]
        left, right = _extent(line, 0)
        number = _MARGIN_NUMBER.fullmatch(source)
        inline = _INLINE_NUMBER.match(source)
        if number and right < body_left:
            target = _same_row(line, lines)
            if target is not None:
                row = target.text_segments[0].start
                edits += [(start, end, ""), (row, row, f"〔第{int(number[1])}項〕 ")]
        elif inline and left < body_left - _MARGIN_GAP:
            edits.append((start, start + inline.end(), f"〔第{int(inline[1])}項〕 "))
    return edits


def _drop_unreliable_items(text: str) -> str:
    """移除無法確認的項次：標在條以外，或同一條不是從 1 連號到至少 2；標錯比不標更糟。"""
    lines = text.split("\n")

    def remove(indexes: list[int]) -> None:
        for index in indexes:
            lines[index] = _ITEM_LABEL.sub("", lines[index], count=1)

    def settle(indexes: list[int], article: str) -> None:
        numbers = [int(_ITEM_LABEL.match(lines[index])[1]) for index in indexes]
        if numbers and (len(numbers) < 2 or numbers != list(range(1, len(numbers) + 1))):
            remove(indexes)
        else:
            for index in indexes:
                lines[index] = lines[index].replace("〔", f"〔{article}", 1)

    article = ""
    labelled: list[int] = []
    for index, line in enumerate(lines):
        if _CHAPTER.match(line) or _SECTION.match(line):
            settle(labelled, article)
            labelled = []
            article = ""
        elif _ARTICLE.fullmatch(line):
            heading = re.sub(r"\s+", "", line)
            # 換頁時重印同一條號，後面的項次仍屬於這一條
            if heading != article:
                settle(labelled, article)
                labelled = []
                article = heading
        elif _ITEM_LABEL.match(line):
            if article:
                labelled.append(index)
            else:
                remove([index])
    settle(labelled, article)
    return "\n".join(lines)


def restore_item_numbers(result: OcrResult) -> str:
    """把法規 PDF 左側印的項次併回內文，在每項第一行標示「第N條第M項」。

    OCR 文字依閱讀順序排列，左側項次常被擠成一串孤立數字，無法看出屬於哪一行；
    因此依各行座標對回同一列的內文。只採用 PDF 實際印出的數字，不從文字推算；
    沒有座標或沒有項次的文件維持原文。

    Args:
        result: Document AI 辨識結果。

    Returns:
        標示項次後的文字。
    """
    text = result.text or ""
    edits = [edit for page in result.pages for edit in _page_item_edits(page, text)]
    for start, end, value in sorted(edits, reverse=True):
        text = f"{text[:start]}{value}{text[end:]}"
    return _drop_unreliable_items(text)


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
        return ExtractedText((ExtractedPage(1, restore_item_numbers(result)),))

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


def _outside_label(unit: str, position: int) -> int:
    """切點落在程式標示中間時退回標示開頭；標示被切開就認不出屬於哪一項。"""
    opening = unit.rfind("〔", 0, position)
    label = _ITEM_IDENTITY.match(unit, opening) if opening >= 0 else None
    return opening if label and position < label.end() else position


def _split_long(unit: str, size: int, overlap: int) -> list[str]:
    """把超過 size 的單位切開：優先在後半段的句末切，相鄰兩段重疊 overlap 字，不切開程式標示。"""
    pieces: list[str] = []
    start = 0
    while start < len(unit):
        end = min(start + size, len(unit))
        if end < len(unit):
            window = unit[start + size // 2 : end]
            cut = max(window.rfind(mark) for mark in _SENTENCE_END)
            if cut >= 0:
                end = start + size // 2 + cut + 1
            end = max(_outside_label(unit, end), start + 1)
        piece = unit[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(unit):
            break
        start = max(_outside_label(unit, end - overlap), start + 1)
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


def _heading(line: str) -> str:
    return re.sub(r"\s+", "", line)[:_HEADING_MAX_CHARS]


def _identity_after(text: str, article: str, item: str) -> tuple[str, str]:
    """從已知的條項往後讀，回傳讀完這段文字時所屬的條與項。"""
    for line in text.split("\n"):
        identity = _ITEM_IDENTITY.match(line)
        if _CHAPTER.match(line) or _SECTION.match(line):
            article, item = "", ""
        elif _ARTICLE.fullmatch(line):
            article, item = re.sub(r"\s+", "", line), ""
        elif identity:
            article, item = identity[1], f"第{identity[2]}項"
    return article, item


def _shared_length(previous: str, chunk: str) -> int:
    """重疊切段時，這段開頭和上一段結尾相同的字數。"""
    for size in range(min(len(previous), len(chunk)), 0, -1):
        if previous.endswith(chunk[:size]):
            return size
    return 0


def add_heading_context(chunks: list[str], title: str | None = None) -> list[str]:
    """在每段前面加上「《文件名稱》章／節」，讓檢索能對上所屬章節（條文本身常不寫章節）。

    章節以段落中第一行內文所屬的章、節為準；遇到新的章會清除原本的節。
    段落不是從條標題或項次開始時，再標「（接續第N條第M項）」；條項只取標題與已確認的項次，
    不從內文引用的條號推斷，無法確認時不標。
    標題去除空白（例：第 四 章 稅額計算 → 第四章稅額計算），方便文字比對。

    Args:
        chunks: split_into_chunks 的結果。
        title: 文件名稱；None 時不加書名。

    Returns:
        加上前綴的段落；沒有書名也沒有章節的段落維持原樣。
    """
    chapter = section = article = item = previous = ""
    result = []
    for chunk in chunks:
        # 重疊切段的開頭還在上一段裡，條項要算到這段真正開始的位置
        shared = _shared_length(previous, chunk)
        article, item = _identity_after(previous[: len(previous) - shared], article, item)
        previous = chunk
        start_article, start_item = article, item
        context: str | None = None
        continued = ""
        for line in chunk.split("\n"):
            if _CHAPTER.match(line):
                chapter, section, article, item = _heading(line), "", "", ""
                continue
            if _SECTION.match(line):
                section, article, item = _heading(line), "", ""
                continue
            heading = _ARTICLE.fullmatch(line)
            identity = _ITEM_IDENTITY.match(line)
            if context is None and line.strip():
                context = "／".join(part for part in (chapter, section) if part)
                # 重疊切段時，段落開頭可能早於目前的項次，這種情況無法確認所屬條項
                current = f"〔{article}{item}〕 "
                if article and not heading and not identity and current not in chunk:
                    continued = f"（接續{article}{item}）"
        article, item = start_article, start_item
        if context is None:
            context = "／".join(part for part in (chapter, section) if part)
        prefix = (f"《{title}》" if title else "") + context + continued
        result.append(f"{prefix}\n{chunk}" if prefix else chunk)
    return result


def prepare_chunks(
    text: str,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    *,
    title: str | None = None,
) -> tuple[ChunkDraft, ...]:
    """產生待 embedding 的段落資料（chunk_index 從 0 開始），每段前面標示文件名稱與章節。

    Args:
        text: 文件文字。
        size: 每段最多字元數（不含前綴）。
        overlap: 長段重疊字元數。
        title: 文件名稱。

    Returns:
        待 embedding 的段落。
    """
    chunks = add_heading_context(split_into_chunks(text, size, overlap), title)
    return tuple(ChunkDraft(index, content, len(content)) for index, content in enumerate(chunks))


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
    drafts = prepare_chunks(text, size, overlap, title=rag_file.title)
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
