"""模組名稱：app.db.repositories.rag_repository
功能說明：以固定 SQL 及綁定參數存取 Schema v1.8 的 RAG 表（知識庫、部門選用、文件、索引工作、段落與檢索），交易由 service 控制。
注意事項：
    - 不新增 ORM Model 或 migration；表結構由 API／專案負責人管理。
    - 向量以 CAST(:embedding AS extensions.vector) 寫入、OPERATOR(extensions.<=>) 比對，
      寫法依 docs/RAG_DEVELOPMENT.md；v1.8 ALTER 尚未執行，向量固定 vector(1536)。
    - repository 不 commit／rollback；UUID 參數一律在 SQL 中 CAST，回傳時轉為字串。
"""

from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError as IntegrityError
from sqlalchemy.exc import SQLAlchemyError as SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_engine
from app.schemas.rag import (
    DepartmentAccess,
    DepartmentOption,
    IndexedChunk,
    KnowledgeBase,
    LatestJob,
    RagFile,
    RagJob,
    RetrievalCandidate,
    SearchHit,
)

_KB_COLUMNS = """knowledge_base_id::text AS knowledge_base_id, knowledge_name, approval_status,
    is_active, submitted_by::text AS submitted_by,
    submitted_department_id::text AS submitted_department_id, reviewed_by::text AS reviewed_by,
    reviewed_at, review_comment, knowledge_code, file_count"""
_ACCESS_COLUMNS = """knowledge_base_id::text AS knowledge_base_id,
    department_id::text AS department_id, can_read, can_manage, use_for_qa"""
_FILE_COLUMNS = """rag_file_id::text AS rag_file_id,
    title, file_version, parse_status, is_approved, approved_by::text AS approved_by, approved_at,
    owner_department_id::text AS owner_department_id, mime_type, bucket_name, storage_path,
    original_filename, size_bytes, created_at, source_type, effective_date,
    effective_date_status, source_note, content_sha256"""
_JOB_COLUMNS = """job_id::text AS job_id, rag_file_id::text AS rag_file_id, job_type, job_status,
    attempts, error_message, started_at, finished_at"""

LIST_KNOWLEDGE_BASES = text(
    f"SELECT {_KB_COLUMNS} FROM public.rag_knowledge_bases ORDER BY knowledge_name, knowledge_base_id"
)
LOCK_KNOWLEDGE_BASE = text(f"""
    SELECT {_KB_COLUMNS} FROM public.rag_knowledge_bases
    WHERE knowledge_base_id = CAST(:id AS uuid) FOR UPDATE
""")
UPDATE_KNOWLEDGE_BASE_REVIEW = text("""
    UPDATE public.rag_knowledge_bases
    SET approval_status = :approval_status,
        submitted_by = CAST(:submitted_by AS uuid),
        submitted_department_id = CAST(:submitted_department_id AS uuid),
        reviewed_by = CAST(:reviewed_by AS uuid),
        reviewed_at = :reviewed_at,
        review_comment = :review_comment,
        updated_at = now()
    WHERE knowledge_base_id = CAST(:id AS uuid)
""")
# 查無資料次數（Schema v2.0 rag_no_answer_stats）：只有每日、每個知識庫的次數，不存問題、回答或使用者
INCREMENT_NO_ANSWER = text("""
    INSERT INTO public.rag_no_answer_stats (stat_date, knowledge_base_id, no_answer_count)
    VALUES (CAST(:stat_date AS date), CAST(:id AS uuid), 1)
    ON CONFLICT (stat_date, knowledge_base_id) DO UPDATE
    SET no_answer_count = public.rag_no_answer_stats.no_answer_count + 1,
        updated_at = pg_catalog.clock_timestamp()
""")
SUM_NO_ANSWER_SINCE = text("""
    SELECT knowledge_base_id::text AS knowledge_base_id, SUM(no_answer_count)::integer AS total
    FROM public.rag_no_answer_stats
    WHERE stat_date >= CAST(:since AS date)
    GROUP BY knowledge_base_id
""")
NO_ANSWER_TABLE_EXISTS = text("SELECT to_regclass('public.rag_no_answer_stats') IS NOT NULL")
LIST_QA_KNOWLEDGE_BASE_IDS = text("""
    SELECT knowledge_base_id::text AS knowledge_base_id
    FROM public.rag_department_access
    WHERE department_id = CAST(:department_id AS uuid) AND use_for_qa
    ORDER BY knowledge_base_id
""")
LIST_ACCESS = text(
    f"SELECT {_ACCESS_COLUMNS} FROM public.rag_department_access ORDER BY department_id"
)
LOCK_DEPARTMENT_ACCESS = text(f"""
    SELECT {_ACCESS_COLUMNS} FROM public.rag_department_access
    WHERE department_id = CAST(:department_id AS uuid) FOR UPDATE
""")
LOCK_KNOWLEDGE_BASE_ACCESS = text(f"""
    SELECT {_ACCESS_COLUMNS} FROM public.rag_department_access
    WHERE knowledge_base_id = CAST(:id AS uuid) FOR UPDATE
""")
CLEAR_QA_FOR_KNOWLEDGE_BASE = text("""
    UPDATE public.rag_department_access SET use_for_qa = false
    WHERE knowledge_base_id = CAST(:id AS uuid) AND use_for_qa
""")
CLEAR_QA_FOR_DEPARTMENT = text("""
    UPDATE public.rag_department_access SET use_for_qa = false
    WHERE department_id = CAST(:department_id AS uuid) AND use_for_qa
""")
SET_QA = text("""
    UPDATE public.rag_department_access SET use_for_qa = true
    WHERE knowledge_base_id = CAST(:id AS uuid) AND department_id = CAST(:department_id AS uuid)
      AND can_read
""")
LIST_FILES = text(f"""
    SELECT {_FILE_COLUMNS} FROM public.rag_files
    WHERE rag_file_id IN (SELECT rag_file_id FROM public.rag_knowledge_base_files
                          WHERE knowledge_base_id = CAST(:id AS uuid))
    ORDER BY created_at, rag_file_id
""")
LOCK_FILE = text(
    f"SELECT {_FILE_COLUMNS} FROM public.rag_files WHERE rag_file_id = CAST(:id AS uuid) FOR UPDATE"
)
UPDATE_FILE_APPROVAL = text("""
    UPDATE public.rag_files
    SET is_approved = :is_approved, approved_by = CAST(:approved_by AS uuid),
        approved_at = :approved_at, updated_at = now()
    WHERE rag_file_id = CAST(:id AS uuid)
""")
REPLACE_FILE_CONTENT = text("""
    UPDATE public.rag_files
    SET file_version = :file_version, is_approved = false, approved_by = NULL, approved_at = NULL,
        parse_status = :parse_status, bucket_name = :bucket_name, storage_path = :storage_path,
        original_filename = :original_filename, mime_type = :mime_type, size_bytes = :size_bytes,
        content_sha256 = :content_sha256,
        updated_at = now()
    WHERE rag_file_id = CAST(:id AS uuid) AND file_version = :file_version - 1
""")
UPDATE_PARSE_STATUS = text("""
    UPDATE public.rag_files SET parse_status = :parse_status, updated_at = now()
    WHERE rag_file_id = CAST(:id AS uuid) AND file_version = :file_version
""")
INSERT_JOB = text("""
    INSERT INTO public.rag_jobs (rag_file_id, job_type, job_status)
    VALUES (CAST(:rag_file_id AS uuid), :job_type, :job_status) RETURNING job_id::text
""")
GET_JOB = text(f"SELECT {_JOB_COLUMNS} FROM public.rag_jobs WHERE job_id = CAST(:id AS uuid)")
LOCK_JOB = text(f"""
    SELECT {_JOB_COLUMNS} FROM public.rag_jobs
    WHERE job_id = CAST(:id AS uuid) FOR UPDATE SKIP LOCKED
""")
UPDATE_JOB = text("""
    UPDATE public.rag_jobs
    SET job_status = :job_status, attempts = :attempts, error_message = :error_message,
        started_at = :started_at, finished_at = :finished_at
    WHERE job_id = CAST(:id AS uuid)
""")
LOCK_FILE_VERSION = text("""
    SELECT file_version FROM public.rag_files WHERE rag_file_id = CAST(:id AS uuid) FOR UPDATE
""")
DELETE_CHUNKS = text("DELETE FROM public.rag_chunks WHERE rag_file_id = CAST(:id AS uuid)")
INSERT_CHUNK = text("""
    INSERT INTO public.rag_chunks (rag_file_id, chunk_index, content, embedding_model, embedding)
    VALUES (CAST(:id AS uuid), :chunk_index, :content, :embedding_model,
            CAST(:embedding AS extensions.vector))
""")
HAS_QA_ACCESS = text("""
    SELECT EXISTS (
        SELECT 1 FROM public.v_user_department_rag_access
        WHERE user_id = CAST(:user_id AS uuid) AND department_id = CAST(:department_id AS uuid)
          AND can_read AND use_for_qa
    )
""")
# 範圍先依檔案分組，保留全部已授權的來源名稱；部門與資訊部測試套用相同核准條件。
_DEPARTMENT_SCOPE = """
    JOIN public.rag_department_access da ON da.knowledge_base_id = kb.knowledge_base_id
     AND da.department_id = CAST(:department_id AS uuid) AND da.can_read AND da.use_for_qa
"""
_REVIEW_SCOPE = "WHERE kb.knowledge_base_id = ANY(CAST(:ids AS uuid[]))"


def _file_scope(*, review: bool = False) -> str:
    """產生固定的授權檔案 CTE；僅由程式選擇固定 SQL，不接受請求片段。"""
    access = _REVIEW_SCOPE if review else _DEPARTMENT_SCOPE + " WHERE true"
    return f"""WITH scoped_files AS (
        SELECT l.rag_file_id,
               array_agg(kb.knowledge_base_id::text ORDER BY kb.knowledge_name, kb.knowledge_base_id) AS knowledge_base_ids,
               array_agg(kb.knowledge_name ORDER BY kb.knowledge_name, kb.knowledge_base_id) AS knowledge_names,
               min(kb.file_count) AS file_count
        FROM public.rag_knowledge_base_files l
        JOIN public.rag_knowledge_bases kb ON kb.knowledge_base_id = l.knowledge_base_id
        {access}
          AND kb.approval_status = 'approved' AND kb.is_active AND kb.file_count >= 1
        GROUP BY l.rag_file_id
    )"""


_HIT_SELECT = """
    SELECT c.content, {distance} AS distance, f.title AS file_title, f.file_version,
           c.chunk_index, sf.knowledge_names, f.rag_file_id::text AS rag_file_id,
           f.effective_date
    FROM scoped_files sf
    JOIN public.rag_files f ON f.rag_file_id = sf.rag_file_id
    JOIN public.rag_chunks c ON c.rag_file_id = f.rag_file_id
    WHERE f.is_approved AND f.parse_status = 'ready'
"""
_DISTANCE = "c.embedding OPERATOR(extensions.<=>) CAST(:query_embedding AS extensions.vector)"
SEARCH_CHUNKS = text(
    _file_scope()
    + f"""
    SELECT c.chunk_id::text AS chunk_id, c.rag_file_id::text AS rag_file_id, c.chunk_index,
           c.content, c.embedding_model, {_DISTANCE} AS distance,
           f.title AS file_title, f.file_version, sf.knowledge_base_ids, sf.knowledge_names,
           'approved' AS approval_status, true AS is_active, true AS can_read, true AS use_for_qa,
           sf.file_count, f.is_approved, f.parse_status, f.effective_date
    FROM scoped_files sf
    JOIN public.rag_files f ON f.rag_file_id = sf.rag_file_id
    JOIN public.rag_chunks c ON c.rag_file_id = f.rag_file_id
    WHERE f.is_approved AND f.parse_status = 'ready'
      AND c.embedding_model = :embedding_model AND c.embedding IS NOT NULL
    ORDER BY distance, c.chunk_id LIMIT :top_k
"""
)


LIST_DEPARTMENTS = text("""
    SELECT department_id::text AS department_id, department_code, department_name
    FROM public.departments WHERE is_active ORDER BY department_code, department_id
""")
GET_KNOWLEDGE_BASE = text(
    f"SELECT {_KB_COLUMNS} FROM public.rag_knowledge_bases WHERE knowledge_base_id = CAST(:id AS uuid)"
)
INSERT_KNOWLEDGE_BASE = text(f"""
    INSERT INTO public.rag_knowledge_bases
        (knowledge_code, knowledge_name, created_by, approval_status, submitted_by,
         submitted_department_id)
    VALUES (:knowledge_code, :knowledge_name, CAST(:user_id AS uuid), 'pending',
            CAST(:user_id AS uuid), CAST(:department_id AS uuid))
    RETURNING {_KB_COLUMNS}
""")
RENAME_KNOWLEDGE_BASE = text("""
    UPDATE public.rag_knowledge_bases SET knowledge_name = :knowledge_name, updated_at = now()
    WHERE knowledge_base_id = CAST(:id AS uuid)
""")
DELETE_KNOWLEDGE_BASE = text(
    "DELETE FROM public.rag_knowledge_bases WHERE knowledge_base_id = CAST(:id AS uuid)"
)
UPSERT_ACCESS = text("""
    INSERT INTO public.rag_department_access
        (knowledge_base_id, department_id, can_read, can_manage)
    VALUES (CAST(:id AS uuid), CAST(:department_id AS uuid), true, :can_manage)
    ON CONFLICT (knowledge_base_id, department_id)
    DO UPDATE SET can_read = true, updated_at = now()
""")
DELETE_ACCESS = text("""
    DELETE FROM public.rag_department_access
    WHERE knowledge_base_id = CAST(:id AS uuid) AND department_id = CAST(:department_id AS uuid)
""")
LIST_KNOWLEDGE_BASE_ACCESS = text(f"""
    SELECT {_ACCESS_COLUMNS} FROM public.rag_department_access
    WHERE knowledge_base_id = CAST(:id AS uuid) ORDER BY department_id
""")
GET_FILE = text(
    f"SELECT {_FILE_COLUMNS} FROM public.rag_files WHERE rag_file_id = CAST(:id AS uuid)"
)
INSERT_FILE = text("""
    INSERT INTO public.rag_files
        (rag_file_id, bucket_name, storage_path, original_filename, mime_type,
         uploaded_by, title, owner_department_id, size_bytes, source_type, effective_date,
         effective_date_status, source_note, content_sha256)
    VALUES (CAST(:id AS uuid), :bucket_name, :storage_path,
            :original_filename, :mime_type, CAST(:uploaded_by AS uuid), :title,
            CAST(:owner_department_id AS uuid), :size_bytes, :source_type, :effective_date,
            :effective_date_status, :source_note, :content_sha256)
""")
RENAME_FILE = text("""
    UPDATE public.rag_files SET title = :title, updated_at = now()
    WHERE rag_file_id = CAST(:id AS uuid)
""")
DELETE_FILE = text("DELETE FROM public.rag_files WHERE rag_file_id = CAST(:id AS uuid)")
LIST_LATEST_JOBS = text("""
    SELECT DISTINCT ON (j.rag_file_id) j.rag_file_id::text AS rag_file_id, j.job_status,
           j.error_message
    FROM public.rag_jobs j
    JOIN public.rag_files f ON f.rag_file_id = j.rag_file_id
    WHERE f.rag_file_id IN (SELECT rag_file_id FROM public.rag_knowledge_base_files
                            WHERE knowledge_base_id = CAST(:id AS uuid))
    ORDER BY j.rag_file_id, j.created_at DESC, j.job_id
""")
# 資訊部測試同樣只接受已核准、啟用且非空的知識庫，以及已核准、ready 的文件。
SEARCH_REVIEW_CHUNKS = text(
    _file_scope(review=True)
    + _HIT_SELECT.format(distance=_DISTANCE)
    + """
      AND c.embedding_model = :embedding_model AND c.embedding IS NOT NULL
    ORDER BY distance, c.chunk_id LIMIT :top_k
"""
)
SEARCH_REVIEW_KEYWORDS = text(
    _file_scope(review=True)
    + _HIT_SELECT.format(distance="0.0")
    + r"""
      AND regexp_replace(c.content, '\s', '', 'g') ~ ALL (CAST(:patterns AS text[]))
    ORDER BY f.title, c.chunk_index, c.chunk_id LIMIT :top_k
"""
)
SEARCH_KEYWORDS = text(
    _file_scope()
    + _HIT_SELECT.format(distance="0.0")
    + r"""
      AND regexp_replace(c.content, '\s', '', 'g') ~ ALL (CAST(:patterns AS text[]))
    ORDER BY f.title, c.chunk_index, c.chunk_id LIMIT :top_k
"""
)
# 重新排入索引：把知識庫內已完成或失敗的文件改回等待索引（切段規則調整後重建用）
QUEUE_REINDEX = text("""
    UPDATE public.rag_files SET parse_status = 'uploaded', updated_at = now()
    WHERE rag_file_id IN (SELECT rag_file_id FROM public.rag_knowledge_base_files
                          WHERE knowledge_base_id = CAST(:id AS uuid))
      AND parse_status IN ('ready', 'failed')
""")
# 待建立索引的文件（uploaded／failed），依上傳先後
LIST_FILES_TO_INDEX = text("""
    SELECT l.knowledge_base_id::text AS knowledge_base_id, f.rag_file_id::text AS rag_file_id
    FROM public.rag_knowledge_base_files l
    JOIN public.rag_files f ON f.rag_file_id = l.rag_file_id WHERE parse_status IN ('uploaded', 'failed')
    ORDER BY created_at, rag_file_id
""")
FIND_PENDING_JOB = text("""
    SELECT job_id::text FROM public.rag_jobs
    WHERE rag_file_id = CAST(:id AS uuid) AND job_status = 'pending'
    ORDER BY created_at, job_id LIMIT 1
""")


def open_session() -> Session:
    """使用既有 Session 工廠開啟連線，由 service 關閉。"""
    return SessionLocal(bind=get_engine())


def vector_literal(values: Sequence[float]) -> str:
    """把向量轉成 pgvector 可接受的文字格式（例如 [0.1,0.2]），不需要 pgvector Python 套件。"""
    return "[" + ",".join(repr(float(value)) for value in values) + "]"


def _search_hit(row: Any) -> SearchHit:
    names = tuple(row["knowledge_names"])
    return SearchHit(
        **{
            **row,
            "distance": float(row["distance"]),
            "knowledge_name": "、".join(names),
            "knowledge_names": names,
        }
    )


def list_knowledge_bases(db: Session) -> list[KnowledgeBase]:
    """列出全部知識庫。"""
    return [KnowledgeBase(**row) for row in db.execute(LIST_KNOWLEDGE_BASES).mappings()]


def lock_knowledge_base(db: Session, knowledge_base_id: str) -> KnowledgeBase | None:
    """鎖定並讀取一個知識庫；不存在時為 None。"""
    row = db.execute(LOCK_KNOWLEDGE_BASE, {"id": knowledge_base_id}).mappings().one_or_none()
    return None if row is None else KnowledgeBase(**row)


def update_knowledge_base_review(db: Session, kb: KnowledgeBase) -> None:
    """寫入審核欄位（approval_status、submitted_*、reviewed_*、review_comment），不提交交易。"""
    db.execute(
        UPDATE_KNOWLEDGE_BASE_REVIEW,
        {
            "id": kb.knowledge_base_id,
            "approval_status": kb.approval_status,
            "submitted_by": kb.submitted_by,
            "submitted_department_id": kb.submitted_department_id,
            "reviewed_by": kb.reviewed_by,
            "reviewed_at": kb.reviewed_at,
            "review_comment": kb.review_comment,
        },
    )


def list_department_access(db: Session) -> tuple[DepartmentAccess, ...]:
    """列出全部部門 × 知識庫的指派與選用。"""
    return tuple(DepartmentAccess(**row) for row in db.execute(LIST_ACCESS).mappings())


def lock_department_access(db: Session, department_id: str) -> tuple[DepartmentAccess, ...]:
    """鎖定一個部門的所有知識庫列（切換問答選用前使用，避免同時切換）。"""
    rows = db.execute(LOCK_DEPARTMENT_ACCESS, {"department_id": department_id}).mappings()
    return tuple(DepartmentAccess(**row) for row in rows)


def lock_knowledge_base_access(db: Session, knowledge_base_id: str) -> tuple[DepartmentAccess, ...]:
    """鎖定一個知識庫的所有部門列。"""
    rows = db.execute(LOCK_KNOWLEDGE_BASE_ACCESS, {"id": knowledge_base_id}).mappings()
    return tuple(DepartmentAccess(**row) for row in rows)


def clear_qa_for_knowledge_base(db: Session, knowledge_base_id: str) -> None:
    """取消所有部門對此知識庫的問答選用（退回時同一交易執行）。"""
    db.execute(CLEAR_QA_FOR_KNOWLEDGE_BASE, {"id": knowledge_base_id})


def clear_qa_for_department(db: Session, department_id: str) -> None:
    """取消部門目前的問答選用（切換時先關舊）。"""
    db.execute(CLEAR_QA_FOR_DEPARTMENT, {"department_id": department_id})


def set_qa(db: Session, knowledge_base_id: str, department_id: str) -> int:
    """開啟部門對知識庫的問答選用（切換時後開新）；回傳更新列數。"""
    result = db.execute(SET_QA, {"id": knowledge_base_id, "department_id": department_id})
    return int(getattr(result, "rowcount", 0) or 0)


def list_files(db: Session, knowledge_base_id: str) -> list[RagFile]:
    """列出知識庫的文件。"""
    return [RagFile(**row) for row in db.execute(LIST_FILES, {"id": knowledge_base_id}).mappings()]


def lock_file(db: Session, rag_file_id: str) -> RagFile | None:
    """鎖定並讀取文件；不存在時為 None。"""
    row = db.execute(LOCK_FILE, {"id": rag_file_id}).mappings().one_or_none()
    return None if row is None else RagFile(**row)


def update_file_approval(db: Session, rag_file: RagFile) -> None:
    """寫入文件核准欄位，不提交交易。"""
    db.execute(
        UPDATE_FILE_APPROVAL,
        {
            "id": rag_file.rag_file_id,
            "is_approved": rag_file.is_approved,
            "approved_by": rag_file.approved_by,
            "approved_at": rag_file.approved_at,
        },
    )


def replace_file_content(db: Session, rag_file: RagFile) -> int:
    """內容替換後寫入新版本與新物件位置（以舊版本為條件）；回傳更新列數，0 代表版本已被他人改變。"""
    result = db.execute(
        REPLACE_FILE_CONTENT,
        {
            "id": rag_file.rag_file_id,
            "file_version": rag_file.file_version,
            "parse_status": rag_file.parse_status,
            "bucket_name": rag_file.bucket_name,
            "storage_path": rag_file.storage_path,
            "original_filename": rag_file.original_filename,
            "mime_type": rag_file.mime_type,
            "size_bytes": rag_file.size_bytes,
            "content_sha256": rag_file.content_sha256,
        },
    )
    return int(getattr(result, "rowcount", 0) or 0)


def update_parse_status(
    db: Session, rag_file_id: str, file_version: int, parse_status: str
) -> None:
    """只在版本相同時更新索引進度（新版本的狀態不被舊工作覆蓋）。"""
    db.execute(
        UPDATE_PARSE_STATUS,
        {"id": rag_file_id, "file_version": file_version, "parse_status": parse_status},
    )


def insert_job(db: Session, job: RagJob) -> str:
    """新增索引工作並回傳 job_id。"""
    return str(
        db.execute(
            INSERT_JOB,
            {
                "rag_file_id": job.rag_file_id,
                "job_type": job.job_type,
                "job_status": job.job_status,
            },
        ).scalar_one()
    )


def get_job(db: Session, job_id: str) -> RagJob | None:
    """讀取工作（不鎖定），先知道對應檔案才能依序鎖定；不存在時為 None。"""
    row = db.execute(GET_JOB, {"id": job_id}).mappings().one_or_none()
    return None if row is None else RagJob(**row)


def lock_job(db: Session, job_id: str) -> RagJob | None:
    """鎖定工作（SKIP LOCKED：他人處理中時視為取不到）；不存在或鎖定中為 None。"""
    row = db.execute(LOCK_JOB, {"id": job_id}).mappings().one_or_none()
    return None if row is None else RagJob(**row)


def update_job(db: Session, job: RagJob) -> None:
    """寫入工作狀態；error_message 只放固定代碼。"""
    db.execute(
        UPDATE_JOB,
        {
            "id": job.job_id,
            "job_status": job.job_status,
            "attempts": job.attempts,
            "error_message": job.error_message,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        },
    )


def lock_file_version(db: Session, rag_file_id: str) -> int | None:
    """寫入段落前鎖定文件並讀取目前版本；文件已刪除時為 None。"""
    value = db.execute(LOCK_FILE_VERSION, {"id": rag_file_id}).scalar_one_or_none()
    return None if value is None else int(value)


def replace_chunks(db: Session, rag_file_id: str, chunks: Sequence[IndexedChunk]) -> None:
    """刪除文件舊段落並一次批次寫入新段落（減少與雲端 DB 的來回），不提交交易。"""
    db.execute(DELETE_CHUNKS, {"id": rag_file_id})
    rows = [
        {
            "id": rag_file_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "embedding_model": chunk.embedding_model,
            "embedding": vector_literal(chunk.embedding),
        }
        for chunk in chunks
    ]
    if rows:
        db.execute(INSERT_CHUNK, rows)


def has_qa_access(db: Session, user_id: UUID, department_id: UUID) -> bool:
    """已驗證的使用者在目前部門是否有選用中的可讀知識庫（v_user_department_rag_access）。"""
    params = {"user_id": str(user_id), "department_id": str(department_id)}
    return bool(db.execute(HAS_QA_ACCESS, params).scalar_one())


def search_chunks(
    db: Session,
    query_embedding: Sequence[float],
    embedding_model: str,
    department_id: UUID,
    top_k: int,
) -> list[RetrievalCandidate]:
    """以 pgvector 距離由小到大取出符合四項條件的段落。"""
    params: dict[str, Any] = {
        "query_embedding": vector_literal(query_embedding),
        "embedding_model": embedding_model,
        "department_id": str(department_id),
        "top_k": top_k,
    }
    return [
        RetrievalCandidate(
            **{
                **row,
                "distance": float(row["distance"]),
                "knowledge_base_ids": tuple(row["knowledge_base_ids"]),
                "knowledge_names": tuple(row["knowledge_names"]),
            }
        )
        for row in db.execute(SEARCH_CHUNKS, params).mappings()
    ]


# ---- 知識庫與文件管理（/knowledge）----


def list_departments(db: Session) -> list[DepartmentOption]:
    """列出啟用中的部門。"""
    return [DepartmentOption(**row) for row in db.execute(LIST_DEPARTMENTS).mappings()]


def get_knowledge_base(db: Session, knowledge_base_id: str) -> KnowledgeBase | None:
    """讀取一個知識庫（不鎖定）；不存在時為 None。"""
    row = db.execute(GET_KNOWLEDGE_BASE, {"id": knowledge_base_id}).mappings().one_or_none()
    return None if row is None else KnowledgeBase(**row)


def insert_knowledge_base(
    db: Session, knowledge_code: str, knowledge_name: str, user_id: str, department_id: str
) -> KnowledgeBase:
    """新增知識庫（固定 pending，送審者為建立者與其目前部門）。

    Raises:
        IntegrityError: 名稱或代碼重複。
    """
    params = {
        "knowledge_code": knowledge_code,
        "knowledge_name": knowledge_name,
        "user_id": user_id,
        "department_id": department_id,
    }
    return KnowledgeBase(**db.execute(INSERT_KNOWLEDGE_BASE, params).mappings().one())


def rename_knowledge_base(db: Session, knowledge_base_id: str, knowledge_name: str) -> None:
    """修改知識庫名稱（knowledge_code 不變）。

    Raises:
        IntegrityError: 名稱重複。
    """
    db.execute(RENAME_KNOWLEDGE_BASE, {"id": knowledge_base_id, "knowledge_name": knowledge_name})


def delete_knowledge_base(db: Session, knowledge_base_id: str) -> None:
    """刪除知識庫；部門配置與關聯由外鍵 CASCADE 刪除，檔案、段落與工作保留。"""
    db.execute(DELETE_KNOWLEDGE_BASE, {"id": knowledge_base_id})


def upsert_department_access(
    db: Session, knowledge_base_id: str, department_id: str, *, can_manage: bool = False
) -> None:
    """指派部門（列存在＝已指派、可讀）；已存在時只恢復可讀，不改 can_manage。"""
    db.execute(
        UPSERT_ACCESS,
        {"id": knowledge_base_id, "department_id": department_id, "can_manage": can_manage},
    )


def delete_department_access(db: Session, knowledge_base_id: str, department_id: str) -> None:
    """取消指派部門（刪除列，問答選用隨之取消）。"""
    db.execute(DELETE_ACCESS, {"id": knowledge_base_id, "department_id": department_id})


def list_knowledge_base_access(db: Session, knowledge_base_id: str) -> tuple[DepartmentAccess, ...]:
    """列出一個知識庫的部門配置（不鎖定）。"""
    rows = db.execute(LIST_KNOWLEDGE_BASE_ACCESS, {"id": knowledge_base_id}).mappings()
    return tuple(DepartmentAccess(**row) for row in rows)


def get_file(db: Session, rag_file_id: str) -> RagFile | None:
    """讀取一份文件（不鎖定）；不存在時為 None。"""
    row = db.execute(GET_FILE, {"id": rag_file_id}).mappings().one_or_none()
    return None if row is None else RagFile(**row)


def insert_file(db: Session, rag_file: RagFile, uploaded_by: str) -> None:
    """新增文件（rag_file_id 由 service 產生，物件路徑才能先含 ID）；固定未核准、等待索引。"""
    db.execute(
        INSERT_FILE,
        {
            "id": rag_file.rag_file_id,
            "bucket_name": rag_file.bucket_name,
            "storage_path": rag_file.storage_path,
            "original_filename": rag_file.original_filename,
            "mime_type": rag_file.mime_type,
            "uploaded_by": uploaded_by,
            "title": rag_file.title,
            "owner_department_id": rag_file.owner_department_id,
            "size_bytes": rag_file.size_bytes,
            "content_sha256": rag_file.content_sha256,
            "source_type": rag_file.source_type,
            "effective_date": rag_file.effective_date,
            "effective_date_status": rag_file.effective_date_status,
            "source_note": rag_file.source_note,
        },
    )


def rename_file(db: Session, rag_file_id: str, title: str) -> None:
    """修改文件顯示名稱。"""
    db.execute(RENAME_FILE, {"id": rag_file_id, "title": title})


def delete_file(db: Session, rag_file_id: str) -> None:
    """刪除文件；部門選用、段落與工作由外鍵 CASCADE 一併刪除。"""
    db.execute(DELETE_FILE, {"id": rag_file_id})


def list_latest_jobs(db: Session, knowledge_base_id: str) -> dict[str, LatestJob]:
    """每份文件最近一次索引工作（rag_file_id → 狀態）。"""
    rows = db.execute(LIST_LATEST_JOBS, {"id": knowledge_base_id}).mappings()
    return {row["rag_file_id"]: LatestJob(**row) for row in rows}


def find_pending_job(db: Session, rag_file_id: str) -> str | None:
    """文件最早一筆等待中的工作 job_id；沒有時為 None。"""
    value = db.execute(FIND_PENDING_JOB, {"id": rag_file_id}).scalar_one_or_none()
    return None if value is None else str(value)


def search_review_chunks(
    db: Session,
    query_embedding: Sequence[float],
    embedding_model: str,
    knowledge_base_ids: Sequence[str],
    top_k: int,
) -> list[SearchHit]:
    """資訊部測試：在勾選知識庫的已核准、ready 文件中檢索，共用檔案每段只回一次。"""
    params: dict[str, Any] = {
        "query_embedding": vector_literal(query_embedding),
        "embedding_model": embedding_model,
        "ids": list(knowledge_base_ids),
        "top_k": top_k,
    }
    return [_search_hit(row) for row in db.execute(SEARCH_REVIEW_CHUNKS, params).mappings()]


def list_files_to_index(db: Session) -> dict[str, list[str]]:
    """每個知識庫待建立索引的文件（knowledge_base_id → rag_file_id 清單，依上傳先後）。"""
    result: dict[str, list[str]] = {}
    for row in db.execute(LIST_FILES_TO_INDEX).mappings():
        result.setdefault(row["knowledge_base_id"], []).append(row["rag_file_id"])
    return result


def search_keyword_chunks(
    db: Session,
    patterns: Sequence[str],
    top_k: int,
    *,
    knowledge_base_ids: Sequence[str] | None = None,
    department_id: UUID | None = None,
) -> list[SearchHit]:
    """比對章節條文；指定知識庫清單為資訊部測試，否則依部門指派的四項條件。"""
    params: dict[str, Any] = {
        "patterns": list(patterns),
        "top_k": top_k,
    }
    if knowledge_base_ids is not None:
        statement = SEARCH_REVIEW_KEYWORDS
        params["ids"] = list(knowledge_base_ids)
    else:
        statement = SEARCH_KEYWORDS
        params["department_id"] = str(department_id)
    return [_search_hit(row) for row in db.execute(statement, params).mappings()]


def queue_reindex(db: Session, knowledge_base_id: str) -> int:
    """把知識庫內已完成或失敗的文件改回等待索引；回傳筆數。"""
    result = db.execute(QUEUE_REINDEX, {"id": knowledge_base_id})
    return int(getattr(result, "rowcount", 0) or 0)


LIST_LIBRARY_FILES = text(
    f"SELECT {_FILE_COLUMNS} FROM public.rag_files ORDER BY created_at DESC, rag_file_id"
)
LIST_UNHASHED_FILES = text(
    f"SELECT {_FILE_COLUMNS} FROM public.rag_files WHERE content_sha256 IS NULL ORDER BY rag_file_id"
)
LIST_ALL_LATEST_JOBS = text("""
    SELECT DISTINCT ON (rag_file_id) rag_file_id::text AS rag_file_id, job_status, error_message
    FROM public.rag_jobs ORDER BY rag_file_id, created_at DESC, job_id
""")
LIST_FILE_REFERENCES = text(f"""
    SELECT {_KB_COLUMNS} FROM public.rag_knowledge_bases
    WHERE knowledge_base_id IN (SELECT knowledge_base_id FROM public.rag_knowledge_base_files
                                WHERE rag_file_id = CAST(:id AS uuid))
    ORDER BY knowledge_name, knowledge_base_id
""")
LIST_ALL_FILE_REFERENCES = text(f"""
    SELECT l.rag_file_id::text AS rag_file_id, kb.*
    FROM public.rag_knowledge_base_files l
    JOIN (SELECT {_KB_COLUMNS} FROM public.rag_knowledge_bases) kb
      ON kb.knowledge_base_id = l.knowledge_base_id::text
    ORDER BY kb.knowledge_name, kb.knowledge_base_id
""")
INSERT_FILE_LINK = text("""
    INSERT INTO public.rag_knowledge_base_files (knowledge_base_id, rag_file_id, added_by)
    VALUES (CAST(:kb_id AS uuid), CAST(:file_id AS uuid), CAST(:actor AS uuid))
    ON CONFLICT (knowledge_base_id, rag_file_id) DO NOTHING
""")
DELETE_FILE_LINK = text("""
    DELETE FROM public.rag_knowledge_base_files
    WHERE knowledge_base_id = CAST(:kb_id AS uuid) AND rag_file_id = CAST(:file_id AS uuid)
""")
FIND_DUPLICATE_FILE = text(f"""
    SELECT {_FILE_COLUMNS} FROM public.rag_files
    WHERE content_sha256 = :sha AND rag_file_id <> CAST(:exclude AS uuid)
    ORDER BY rag_file_id LIMIT 1
""")
LOCK_CONTENT_HASH = text(
    "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(:sha, 0))"
)
SET_LEGACY_HASH = text("""
    UPDATE public.rag_files SET content_sha256 = :sha
    WHERE rag_file_id = CAST(:id AS uuid) AND file_version = :version AND content_sha256 IS NULL
""")
GET_DEPARTMENT = text("""
    SELECT department_id::text AS department_id, department_code, department_name
    FROM public.departments WHERE department_id = CAST(:id AS uuid) AND is_active
""")
GET_DEPARTMENT_CODE = text("""
    SELECT department_code FROM public.departments WHERE department_id = CAST(:id AS uuid)
""")


def list_library_files(db: Session) -> list[RagFile]:
    """列出全部獨立檔案；核准與索引狀態不影響清單可見性。"""
    return [RagFile(**row) for row in db.execute(LIST_LIBRARY_FILES).mappings()]


def list_unhashed_files(db: Session) -> list[RagFile]:
    """列出移轉後尚無原檔雜湊的舊檔案，供上傳前核對。"""
    return [RagFile(**row) for row in db.execute(LIST_UNHASHED_FILES).mappings()]


def list_all_latest_jobs(db: Session) -> dict[str, LatestJob]:
    """讀取檔案庫每份檔案最近工作。"""
    return {
        row["rag_file_id"]: LatestJob(**row) for row in db.execute(LIST_ALL_LATEST_JOBS).mappings()
    }


def list_file_references(db: Session, rag_file_id: str) -> tuple[KnowledgeBase, ...]:
    """檔案被哪些知識庫引用，包含未核准或停用知識庫。"""
    return tuple(
        KnowledgeBase(**row)
        for row in db.execute(LIST_FILE_REFERENCES, {"id": rag_file_id}).mappings()
    )


def list_all_file_references(db: Session) -> dict[str, tuple[KnowledgeBase, ...]]:
    """一次讀取全部檔案的引用（rag_file_id → 知識庫），檔案清單不必逐檔查詢。"""
    references: dict[str, tuple[KnowledgeBase, ...]] = {}
    for row in db.execute(LIST_ALL_FILE_REFERENCES).mappings():
        values = dict(row)
        rag_file_id = values.pop("rag_file_id")
        references[rag_file_id] = (*references.get(rag_file_id, ()), KnowledgeBase(**values))
    return references


def insert_file_link(db: Session, knowledge_base_id: str, rag_file_id: str, actor: str) -> None:
    """加入關聯；計數與部門指派僅由資料庫 Trigger 維護。"""
    db.execute(
        INSERT_FILE_LINK, {"kb_id": knowledge_base_id, "file_id": rag_file_id, "actor": actor}
    )


def delete_file_link(db: Session, knowledge_base_id: str, rag_file_id: str) -> None:
    """移除關聯；不刪原檔、不重新索引。"""
    db.execute(DELETE_FILE_LINK, {"kb_id": knowledge_base_id, "file_id": rag_file_id})


def lock_content_hash(db: Session, digest: str) -> None:
    """序列化相同內容的寫入，避免並行上傳通過查重後重複新增。"""
    db.execute(LOCK_CONTENT_HASH, {"sha": digest})


def find_duplicate_file(
    db: Session, digest: str, exclude: str = "00000000-0000-0000-0000-000000000000"
) -> RagFile | None:
    """查詢相同原始內容的檔案，替換時可排除自己。"""
    row = (
        db.execute(FIND_DUPLICATE_FILE, {"sha": digest, "exclude": exclude})
        .mappings()
        .one_or_none()
    )
    return None if row is None else RagFile(**row)


def set_legacy_hash(db: Session, rag_file_id: str, version: int, digest: str) -> None:
    """僅在版本未變時補上舊檔雜湊。"""
    db.execute(SET_LEGACY_HASH, {"id": rag_file_id, "version": version, "sha": digest})


def get_department(db: Session, department_id: str) -> DepartmentOption | None:
    """依已驗證部門 UUID 讀取啟用中部門與上傳路徑代碼。"""
    row = db.execute(GET_DEPARTMENT, {"id": department_id}).mappings().one_or_none()
    return None if row is None else DepartmentOption(**row)


def get_department_code(db: Session, department_id: str) -> str | None:
    """依部門 UUID 讀取代碼，含已停用部門（既有檔案的上傳部門可能已停用）。"""
    value = db.execute(GET_DEPARTMENT_CODE, {"id": department_id}).scalar_one_or_none()
    return None if value is None else str(value)


def list_qa_knowledge_base_ids(db: Session, department_id: str) -> list[str]:
    """列出部門選用問答的知識庫（查無資料次數依此記錄）。"""
    rows = db.execute(LIST_QA_KNOWLEDGE_BASE_IDS, {"department_id": department_id}).mappings()
    return [row["knowledge_base_id"] for row in rows]


def increment_no_answer(db: Session, knowledge_base_ids: Sequence[str], stat_date: date) -> None:
    """當日每個知識庫的查無資料次數各加 1；依 ID 排序寫入避免互鎖，不 commit。"""
    for knowledge_base_id in sorted(set(knowledge_base_ids)):
        db.execute(INCREMENT_NO_ANSWER, {"stat_date": stat_date, "id": knowledge_base_id})


def sum_no_answer_since(db: Session, since: date) -> dict[str, int]:
    """各知識庫自 since（含）起的查無資料次數；資料表不存在時回空（不影響列表）。"""
    if not db.execute(NO_ANSWER_TABLE_EXISTS).scalar_one():
        return {}
    rows = db.execute(SUM_NO_ANSWER_SINCE, {"since": since}).mappings()
    return {row["knowledge_base_id"]: int(row["total"] or 0) for row in rows}


LIST_QA_CHOICES = text("""
    SELECT DISTINCT kb.knowledge_base_id::text AS id, kb.knowledge_name AS title
    FROM public.v_user_department_rag_access va
    JOIN public.rag_knowledge_bases kb ON kb.knowledge_base_id = va.knowledge_base_id
    WHERE va.user_id = CAST(:user_id AS uuid)
      AND va.department_id = CAST(:department_id AS uuid)
      AND va.can_read AND va.use_for_qa
      AND kb.approval_status = 'approved' AND kb.is_active AND kb.file_count >= 1
    ORDER BY title, id
""")


def list_qa_choices(db: Session, user_id: UUID, department_id: UUID) -> list[dict[str, str]]:
    """只列本人目前部門可用的問答知識庫，不回傳其他部門名稱。"""
    return [
        dict(row)
        for row in db.execute(
            LIST_QA_CHOICES, {"user_id": str(user_id), "department_id": str(department_id)}
        ).mappings()
    ]


def qa_department_status(db: Session, user_id: UUID, department_id: UUID) -> list[dict[str, Any]]:
    """唯讀診斷本人目前部門已開放閱讀的知識庫，不回傳其他部門或未授權來源。"""
    statement = text("""
        SELECT kb.knowledge_base_id::text AS id, kb.knowledge_name AS title,
               da.use_for_qa AS selected,
               (kb.approval_status = 'approved' AND kb.is_active AND kb.file_count >= 1) AS eligible
        FROM public.v_user_departments ud
        JOIN public.rag_department_access da ON da.department_id = ud.department_id AND da.can_read
        JOIN public.rag_knowledge_bases kb ON kb.knowledge_base_id = da.knowledge_base_id
        WHERE ud.user_id = CAST(:user_id AS uuid)
          AND ud.department_id = CAST(:department_id AS uuid)
        ORDER BY kb.knowledge_name, kb.knowledge_base_id
    """)
    return [
        dict(row)
        for row in db.execute(
            statement,
            {
                "user_id": str(user_id),
                "department_id": str(department_id),
            },
        ).mappings()
    ]
