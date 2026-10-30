"""模組名稱：app.db.repositories.rag_repository
功能說明：以固定 SQL 及綁定參數存取 Schema v1.6 的 RAG 表（知識庫、部門選用、文件、索引工作、段落與檢索），交易由 service 控制。
注意事項：
    - 不新增 ORM Model 或 migration；表結構由 API／專案負責人管理。
    - 向量以 CAST(:embedding AS extensions.vector) 寫入、OPERATOR(extensions.<=>) 比對，
      寫法依 docs/RAG_DEVELOPMENT.md，尚未在正式 DB 實測；正式 DB 已套用 v1.6（vector(1536)，使用者回報）。
    - repository 不 commit／rollback；UUID 參數一律在 SQL 中 CAST，回傳時轉為字串。
"""

from collections.abc import Sequence
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
    FileDepartmentUsage,
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
    reviewed_at, review_comment, knowledge_code"""
_ACCESS_COLUMNS = """knowledge_base_id::text AS knowledge_base_id,
    department_id::text AS department_id, can_read, can_manage, use_for_qa"""
_FILE_COLUMNS = """rag_file_id::text AS rag_file_id, knowledge_base_id::text AS knowledge_base_id,
    title, file_version, parse_status, is_approved, approved_by::text AS approved_by, approved_at,
    owner_department_id::text AS owner_department_id, mime_type, bucket_name, storage_path,
    original_filename, size_bytes, created_at"""
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
    WHERE knowledge_base_id = CAST(:id AS uuid) ORDER BY created_at, rag_file_id
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
# 檢索五項條件：1 知識庫 approved 且啟用、2 部門 use_for_qa、3 文件於該部門 use_document、
# 4 文件 is_approved、5 parse_status = ready；另限同一 embedding_model
SEARCH_CHUNKS = text("""
    SELECT c.chunk_id::text AS chunk_id, c.rag_file_id::text AS rag_file_id, c.chunk_index,
           c.content, c.embedding_model,
           c.embedding OPERATOR(extensions.<=>) CAST(:query_embedding AS extensions.vector)
               AS distance,
           f.title AS file_title, f.file_version,
           kb.knowledge_base_id::text AS knowledge_base_id, kb.knowledge_name,
           kb.approval_status, kb.is_active, da.use_for_qa, u.use_document,
           f.is_approved, f.parse_status
    FROM public.rag_chunks c
    JOIN public.rag_files f ON f.rag_file_id = c.rag_file_id
    JOIN public.rag_knowledge_bases kb ON kb.knowledge_base_id = f.knowledge_base_id
    JOIN public.rag_department_access da
      ON da.knowledge_base_id = kb.knowledge_base_id
     AND da.department_id = CAST(:department_id AS uuid)
    JOIN public.rag_file_department_usage u
      ON u.rag_file_id = f.rag_file_id AND u.department_id = CAST(:department_id AS uuid)
    WHERE kb.approval_status = 'approved' AND kb.is_active
      AND da.can_read AND da.use_for_qa
      AND u.use_document
      AND f.is_approved
      AND f.parse_status = 'ready'
      AND c.embedding_model = :embedding_model
      AND c.embedding IS NOT NULL
    ORDER BY distance
    LIMIT :top_k
""")


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
        (rag_file_id, knowledge_base_id, bucket_name, storage_path, original_filename, mime_type,
         uploaded_by, title, owner_department_id, size_bytes)
    VALUES (CAST(:id AS uuid), CAST(:knowledge_base_id AS uuid), :bucket_name, :storage_path,
            :original_filename, :mime_type, CAST(:uploaded_by AS uuid), :title,
            CAST(:owner_department_id AS uuid), :size_bytes)
""")
RENAME_FILE = text("""
    UPDATE public.rag_files SET title = :title, updated_at = now()
    WHERE rag_file_id = CAST(:id AS uuid)
""")
DELETE_FILE = text("DELETE FROM public.rag_files WHERE rag_file_id = CAST(:id AS uuid)")
LIST_FILE_USAGE = text("""
    SELECT u.rag_file_id::text AS rag_file_id, u.department_id::text AS department_id,
           u.use_document, u.updated_by::text AS updated_by
    FROM public.rag_file_department_usage u
    JOIN public.rag_files f ON f.rag_file_id = u.rag_file_id
    WHERE f.knowledge_base_id = CAST(:id AS uuid)
    ORDER BY u.rag_file_id, u.department_id
""")
UPSERT_FILE_USAGE = text("""
    INSERT INTO public.rag_file_department_usage
        (rag_file_id, department_id, use_document, updated_by)
    VALUES (CAST(:id AS uuid), CAST(:department_id AS uuid), :use_document,
            CAST(:updated_by AS uuid))
    ON CONFLICT (rag_file_id, department_id)
    DO UPDATE SET use_document = EXCLUDED.use_document, updated_by = EXCLUDED.updated_by,
                  updated_at = now()
""")
LIST_LATEST_JOBS = text("""
    SELECT DISTINCT ON (j.rag_file_id) j.rag_file_id::text AS rag_file_id, j.job_status,
           j.error_message
    FROM public.rag_jobs j
    JOIN public.rag_files f ON f.rag_file_id = j.rag_file_id
    WHERE f.knowledge_base_id = CAST(:id AS uuid)
    ORDER BY j.rag_file_id, j.created_at DESC, j.job_id
""")
# 資訊部測試問答：只限指定知識庫、已完成索引（ready）的文件，不看核准與部門選用；僅供資訊部使用
SEARCH_REVIEW_CHUNKS = text("""
    SELECT c.content,
           c.embedding OPERATOR(extensions.<=>) CAST(:query_embedding AS extensions.vector)
               AS distance,
           f.title AS file_title, f.file_version, c.chunk_index, kb.knowledge_name,
           f.rag_file_id::text AS rag_file_id
    FROM public.rag_chunks c
    JOIN public.rag_files f ON f.rag_file_id = c.rag_file_id
    JOIN public.rag_knowledge_bases kb ON kb.knowledge_base_id = f.knowledge_base_id
    WHERE kb.knowledge_base_id = CAST(:id AS uuid)
      AND f.parse_status = 'ready'
      AND c.embedding_model = :embedding_model
      AND c.embedding IS NOT NULL
    ORDER BY distance
    LIMIT :top_k
""")
# 文字比對：問題提到「第 X 章／節／條」時，找去除空白後符合全部樣式（正規表示式）的段落；
# 與向量檢索合併，補足向量不擅長的章節編號。distance 固定為 0（明確指名，視為相關）
_KEYWORD_SELECT = """
    SELECT c.content, 0.0 AS distance, f.title AS file_title, f.file_version, c.chunk_index,
           kb.knowledge_name, f.rag_file_id::text AS rag_file_id
    FROM public.rag_chunks c
    JOIN public.rag_files f ON f.rag_file_id = c.rag_file_id
    JOIN public.rag_knowledge_bases kb ON kb.knowledge_base_id = f.knowledge_base_id
"""
SEARCH_REVIEW_KEYWORDS = text(f"""{_KEYWORD_SELECT}
    WHERE kb.knowledge_base_id = CAST(:id AS uuid)
      AND f.parse_status = 'ready'
      AND regexp_replace(c.content, '\\s', '', 'g') ~ ALL (CAST(:patterns AS text[]))
    ORDER BY f.title, c.chunk_index
    LIMIT :top_k
""")
# 一般部門：同檢索五項條件
SEARCH_KEYWORDS = text(f"""{_KEYWORD_SELECT}
    JOIN public.rag_department_access da
      ON da.knowledge_base_id = kb.knowledge_base_id
     AND da.department_id = CAST(:department_id AS uuid)
    JOIN public.rag_file_department_usage u
      ON u.rag_file_id = f.rag_file_id AND u.department_id = CAST(:department_id AS uuid)
    WHERE kb.approval_status = 'approved' AND kb.is_active
      AND da.can_read AND da.use_for_qa
      AND u.use_document
      AND f.is_approved
      AND f.parse_status = 'ready'
      AND regexp_replace(c.content, '\\s', '', 'g') ~ ALL (CAST(:patterns AS text[]))
    ORDER BY f.title, c.chunk_index
    LIMIT :top_k
""")
# 重新排入索引：把知識庫內已完成或失敗的文件改回等待索引（切段規則調整後重建用）
QUEUE_REINDEX = text("""
    UPDATE public.rag_files SET parse_status = 'uploaded', updated_at = now()
    WHERE knowledge_base_id = CAST(:id AS uuid) AND parse_status IN ('ready', 'failed')
""")
COUNT_FILES = text("""
    SELECT knowledge_base_id::text AS knowledge_base_id, count(*) AS total
    FROM public.rag_files GROUP BY knowledge_base_id
""")
# 待建立索引的文件（uploaded／failed），依上傳先後
LIST_FILES_TO_INDEX = text("""
    SELECT knowledge_base_id::text AS knowledge_base_id, rag_file_id::text AS rag_file_id
    FROM public.rag_files WHERE parse_status IN ('uploaded', 'failed')
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
    """以 pgvector 距離由小到大取出符合五項條件的段落。"""
    params: dict[str, Any] = {
        "query_embedding": vector_literal(query_embedding),
        "embedding_model": embedding_model,
        "department_id": str(department_id),
        "top_k": top_k,
    }
    return [
        RetrievalCandidate(**{**row, "distance": float(row["distance"])})
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
    """刪除知識庫；部門配置、文件、段落與工作由外鍵 CASCADE 一併刪除。"""
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
            "knowledge_base_id": rag_file.knowledge_base_id,
            "bucket_name": rag_file.bucket_name,
            "storage_path": rag_file.storage_path,
            "original_filename": rag_file.original_filename,
            "mime_type": rag_file.mime_type,
            "uploaded_by": uploaded_by,
            "title": rag_file.title,
            "owner_department_id": rag_file.owner_department_id,
            "size_bytes": rag_file.size_bytes,
        },
    )


def rename_file(db: Session, rag_file_id: str, title: str) -> None:
    """修改文件顯示名稱。"""
    db.execute(RENAME_FILE, {"id": rag_file_id, "title": title})


def delete_file(db: Session, rag_file_id: str) -> None:
    """刪除文件；部門選用、段落與工作由外鍵 CASCADE 一併刪除。"""
    db.execute(DELETE_FILE, {"id": rag_file_id})


def list_file_usage(db: Session, knowledge_base_id: str) -> tuple[FileDepartmentUsage, ...]:
    """列出知識庫內所有文件的部門選用。"""
    rows = db.execute(LIST_FILE_USAGE, {"id": knowledge_base_id}).mappings()
    return tuple(FileDepartmentUsage(**row) for row in rows)


def upsert_file_usage(db: Session, usage: FileDepartmentUsage) -> None:
    """寫入文件在某部門的選用。"""
    db.execute(
        UPSERT_FILE_USAGE,
        {
            "id": usage.rag_file_id,
            "department_id": usage.department_id,
            "use_document": usage.use_document,
            "updated_by": usage.updated_by,
        },
    )


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
    knowledge_base_id: str,
    top_k: int,
) -> list[SearchHit]:
    """資訊部測試：在指定知識庫已完成索引的文件中，依距離由小到大取段落（含未核准文件）。"""
    params: dict[str, Any] = {
        "query_embedding": vector_literal(query_embedding),
        "embedding_model": embedding_model,
        "id": knowledge_base_id,
        "top_k": top_k,
    }
    return [
        SearchHit(**{**row, "distance": float(row["distance"])})
        for row in db.execute(SEARCH_REVIEW_CHUNKS, params).mappings()
    ]


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
    knowledge_base_id: str | None = None,
    department_id: UUID | None = None,
) -> list[SearchHit]:
    """以章節條文樣式（正規表示式，全部都要符合）比對段落；指定知識庫為資訊部測試，否則依部門五項條件。"""
    params: dict[str, Any] = {
        "patterns": list(patterns),
        "top_k": top_k,
    }
    if knowledge_base_id is not None:
        statement = SEARCH_REVIEW_KEYWORDS
        params["id"] = knowledge_base_id
    else:
        statement = SEARCH_KEYWORDS
        params["department_id"] = str(department_id)
    return [
        SearchHit(**{**row, "distance": float(row["distance"])})
        for row in db.execute(statement, params).mappings()
    ]


def queue_reindex(db: Session, knowledge_base_id: str) -> int:
    """把知識庫內已完成或失敗的文件改回等待索引；回傳筆數。"""
    result = db.execute(QUEUE_REINDEX, {"id": knowledge_base_id})
    return int(getattr(result, "rowcount", 0) or 0)


def count_files(db: Session) -> dict[str, int]:
    """每個知識庫的文件數。"""
    return {
        row["knowledge_base_id"]: int(row["total"]) for row in db.execute(COUNT_FILES).mappings()
    }
