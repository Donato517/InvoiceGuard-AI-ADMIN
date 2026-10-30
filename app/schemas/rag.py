"""
模組名稱：app.schemas.rag
功能說明：RAG 知識庫的資料契約——知識庫審核、部門選用、文件、索引工作、切段與檢索結果。
注意事項：
    - 欄位對應 Schema v1.5 的 rag_knowledge_bases、rag_department_access、rag_files、
      rag_file_department_usage、rag_chunks、rag_jobs。
    - 一律為不可變物件；service 回傳新物件，由 repository 在 service 控制的交易中寫入。
    - 畫面只顯示中文標籤，不顯示狀態代碼。
"""

from dataclasses import dataclass
from datetime import datetime

# ---- 知識庫審核（rag_knowledge_bases.approval_status）----
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_LABELS = {
    APPROVAL_PENDING: "待審核",
    APPROVAL_APPROVED: "已核准",
    APPROVAL_REJECTED: "已退回",
}

# ---- 文件索引進度（rag_files.parse_status）；與核准互相獨立 ----
PARSE_UPLOADED = "uploaded"
PARSE_PROCESSING = "processing"
PARSE_READY = "ready"
PARSE_FAILED = "failed"
PARSE_LABELS = {
    PARSE_UPLOADED: "等待索引",
    PARSE_PROCESSING: "索引中",
    PARSE_READY: "已索引",
    PARSE_FAILED: "索引失敗",
}

# ---- 索引工作（rag_jobs）----
JOB_EXTRACT = "extract"
JOB_CHUNK = "chunk"
JOB_EMBED = "embed"
JOB_REINDEX = "reindex"
JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"

# 索引結果
INDEX_READY = "ready"
INDEX_STALE = "stale"
INDEX_FAILED = "failed"

# 索引失敗的固定代碼（寫入 rag_jobs.error_message，不放文件內容）
ERROR_EMPTY_TEXT = "rag_empty_text"
ERROR_VERSION_MISMATCH = "rag_version_mismatch"
ERROR_UNSUPPORTED_FORMAT = "rag_unsupported_format"
ERROR_TEXT_DECODE = "rag_text_decode_failed"
ERROR_STORAGE_READ = "rag_storage_read_failed"
ERROR_PDF_PAGE_LIMIT = "rag_pdf_page_limit"
ERROR_LABELS = {
    ERROR_EMPTY_TEXT: "擷取不到文字（可能是掃描檔或空白文件）",
    ERROR_VERSION_MISMATCH: "文件內容已更新，本次索引結果已放棄",
    ERROR_UNSUPPORTED_FORMAT: "目前不支援此格式（DOCX 尚未實作）",
    ERROR_TEXT_DECODE: "文字檔不是 UTF-8 編碼",
    ERROR_STORAGE_READ: "讀取原始檔案失敗",
    ERROR_PDF_PAGE_LIMIT: "PDF 超過 15 頁，目前尚未支援長文件",
    "document_ai_invalid_input": "PDF 無法辨識（檔案損壞或格式不符）",
    "document_ai_timeout": "PDF 辨識逾時，可稍後重新索引",
    "document_ai_quota_exceeded": "PDF 辨識額度不足，可稍後重新索引",
    "document_ai_unreachable": "無法連線 PDF 辨識服務，可稍後重新索引",
    "document_ai_failed": "PDF 辨識服務錯誤，可稍後重新索引",
    "embedding_timeout": "向量服務逾時，可稍後重新索引",
    "embedding_quota_exceeded": "向量服務額度不足，可稍後重新索引",
    "embedding_unreachable": "無法連線向量服務，可稍後重新索引",
    "embedding_failed": "向量服務錯誤，可稍後重新索引",
    "embedding_invalid_input": "段落超過向量模型上限",
}

# 上傳格式：副檔名 → rag_files.mime_type（bucket rag 允許的類型）
UPLOAD_MIME_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain",
    "md": "text/markdown",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
# 可直接在畫面編輯文字的格式
TEXT_EDITABLE_EXTENSIONS = frozenset({"txt", "md"})


@dataclass(frozen=True)
class RagActor:
    """目前操作者，由路由以已驗證身分建立（module.identity），不接受表單自報。

    Attributes:
        user_id: user_profiles.user_id（UUID 字串），寫入 reviewed_by、uploaded_by 等欄位。
        department_id: 目前選取部門（UUID 字串）。
        is_it: 目前部門是否為資訊部（request.state.is_it_workspace）。
    """

    user_id: str
    department_id: str
    is_it: bool = False


@dataclass(frozen=True)
class DepartmentOption:
    """部門清單的一列（departments，只列啟用中）。"""

    department_id: str
    department_code: str
    department_name: str


@dataclass(frozen=True)
class LatestJob:
    """文件最近一次索引工作的狀態，畫面用來顯示失敗原因與是否可再索引。"""

    rag_file_id: str
    job_status: str
    error_message: str | None = None


class RagRuleError(Exception):
    """RAG 操作不符規則；code 給程式判斷，message 給使用者閱讀。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class KnowledgeBase:
    """一個知識庫（rag_knowledge_bases）。

    Attributes:
        approval_status: pending／approved／rejected；新知識庫固定 pending（含資訊部自己建立）。
        is_active: 另一個開關，可停用已核准的知識庫。
        review_comment: 退回原因。
    """

    knowledge_base_id: str
    knowledge_name: str
    approval_status: str = APPROVAL_PENDING
    is_active: bool = True
    submitted_by: str | None = None
    submitted_department_id: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_comment: str | None = None
    knowledge_code: str | None = None


@dataclass(frozen=True)
class DepartmentAccess:
    """部門 × 知識庫（rag_department_access）；列存在＝已指派。

    Attributes:
        can_read: 該部門可讀；use_for_qa 需 can_read。
        can_manage: 該部門可編輯。
        use_for_qa: 該部門選用此知識庫問答；一個部門同時只選一個。
    """

    knowledge_base_id: str
    department_id: str
    can_read: bool = True
    can_manage: bool = False
    use_for_qa: bool = False


@dataclass(frozen=True)
class RagFile:
    """知識庫中的一份文件（rag_files）。

    Attributes:
        file_version: 內容替換時 +1；索引寫入前須核對。
        parse_status: 索引進度；與 is_approved 互相獨立。
        is_approved: 資訊部核准；新上傳與內容替換後固定 False。
        owner_department_id: 上傳部門。
        size_bytes: 檔案大小。
    """

    rag_file_id: str
    knowledge_base_id: str
    title: str
    file_version: int = 1
    parse_status: str = PARSE_UPLOADED
    is_approved: bool = False
    approved_by: str | None = None
    approved_at: datetime | None = None
    owner_department_id: str | None = None
    mime_type: str | None = None
    bucket_name: str | None = None
    storage_path: str | None = None
    original_filename: str | None = None
    size_bytes: int | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class FileDepartmentUsage:
    """文件 × 部門選用（rag_file_department_usage）。"""

    rag_file_id: str
    department_id: str
    use_document: bool = True
    updated_by: str | None = None


@dataclass(frozen=True)
class RagJob:
    """索引工作（rag_jobs）。

    Attributes:
        job_type: extract／chunk／embed／reindex。
        attempts: 已執行次數；每次開始處理時 +1。
        error_message: 固定錯誤代碼，不放文件內容。
        file_version: 開始處理時記下的文件版本（不是資料表欄位），寫入前與目前版本核對。
    """

    job_id: str
    rag_file_id: str
    job_type: str = JOB_REINDEX
    job_status: str = JOB_PENDING
    attempts: int = 0
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    file_version: int | None = None


@dataclass(frozen=True)
class ExtractedPage:
    """擷取出的一頁文字。"""

    page_number: int
    text: str


@dataclass(frozen=True)
class ExtractedText:
    """一份文件擷取出的文字，依頁排列。"""

    pages: tuple[ExtractedPage, ...]

    @property
    def text(self) -> str:
        """全部頁面依序以空行相接的純文字。"""
        return "\n\n".join(page.text for page in self.pages if page.text.strip())


@dataclass(frozen=True)
class ChunkDraft:
    """待 embedding 的段落（尚未有向量）。

    Attributes:
        chunk_index: 從 0 開始，對應 rag_chunks.chunk_index。
        char_count: 字元數；token 數由模型決定，此處不估算。
    """

    chunk_index: int
    content: str
    char_count: int


@dataclass(frozen=True)
class IndexedChunk:
    """已取得向量、待寫入 rag_chunks 的段落。"""

    chunk_index: int
    content: str
    embedding: tuple[float, ...]
    embedding_model: str


@dataclass(frozen=True)
class IndexResult:
    """一次索引的結果，交給 repository 在同一交易寫入。

    Attributes:
        outcome: ready（寫入 chunks 並設 ready）、stale（版本不符，丟棄）、failed。
        job: 更新後的工作。
        chunks: outcome 為 ready 時要寫入的段落；其他為空。
        parse_status: 要寫回 rag_files 的狀態；stale 時為 None（不更動文件）。
        error_code: 失敗或放棄的固定代碼。
    """

    outcome: str
    job: RagJob
    chunks: tuple[IndexedChunk, ...] = ()
    parse_status: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class PendingReviews:
    """資訊部待審清單。"""

    knowledge_bases: tuple[KnowledgeBase, ...]
    files: tuple[RagFile, ...]


@dataclass(frozen=True)
class RetrievalCandidate:
    """repository 依相似度排序後回傳的一段（含檢索五項條件的欄位，供 service 再次確認）。

    Attributes:
        distance: pgvector <=> 距離，越小越相似；由 DB 計算。
    """

    chunk_id: str
    rag_file_id: str
    chunk_index: int
    content: str
    distance: float
    embedding_model: str
    file_title: str
    file_version: int
    knowledge_base_id: str
    knowledge_name: str
    approval_status: str
    is_active: bool
    use_for_qa: bool
    use_document: bool
    is_approved: bool
    parse_status: str


@dataclass(frozen=True)
class SearchHit:
    """一筆檢索結果與來源。"""

    content: str
    distance: float
    file_title: str
    file_version: int
    chunk_index: int
    knowledge_name: str
    rag_file_id: str


@dataclass(frozen=True)
class SourceDocument:
    """回答引用的來源文件（同一文件同一版本只列一次）。"""

    rag_file_id: str
    file_title: str
    file_version: int
    knowledge_name: str


@dataclass(frozen=True)
class SearchResult:
    """檢索結果。

    Attributes:
        hits: 依 DB 相似度排序的段落。
        sources: 去重後的來源文件。
        excluded_count: repository 回傳但 service 再確認條件不符而排除的筆數（應為 0）。
    """

    hits: tuple[SearchHit, ...]
    sources: tuple[SourceDocument, ...]
    excluded_count: int = 0
