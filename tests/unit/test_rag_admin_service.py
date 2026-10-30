"""
模組名稱：tests.unit.test_rag_admin_service
功能說明：RAG 管理交易測試——越權回滾、退回清除選用、一部門一個（先關舊再開新）、處理索引工作
          （成功、掃描檔／PDF 不可 ready、PDF 超過 15 頁、版本不符丟棄、讀檔失敗重試）、上傳與
          Storage 路徑、內容替換、管理範圍、刪除、DB 無法使用。
注意事項：
    - 以記憶體替身取代 rag_repository 的函式、Session、Storage 與 Document AI，
      不連正式 DB／Storage／Google。
    - 向量用戶端使用 docs/RAG_DEVELOPMENT.md 的 FakeEmbedding 寫法。
"""

import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from app.db.repositories import rag_repository
from app.integrations.document_ai import DocumentAIError
from app.integrations.supabase_storage import StorageError
from app.integrations.vertex_embedding import EmbeddingBatch, EmbeddingTask
from app.schemas.rag import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    ERROR_PDF_PAGE_LIMIT,
    ERROR_STORAGE_READ,
    ERROR_UNSUPPORTED_FORMAT,
    ERROR_VERSION_MISMATCH,
    INDEX_FAILED,
    INDEX_READY,
    INDEX_STALE,
    JOB_FAILED,
    JOB_PENDING,
    JOB_SUCCEEDED,
    PARSE_FAILED,
    PARSE_READY,
    PARSE_UPLOADED,
    DepartmentAccess,
    DepartmentOption,
    FileDepartmentUsage,
    IndexedChunk,
    KnowledgeBase,
    LatestJob,
    RagActor,
    RagFile,
    RagJob,
    RagRuleError,
)
from app.services import rag_admin_service as service
from app.services import rag_indexing_service as indexing

IT_USER = UUID("00000000-0000-4000-8000-0000000000a1")
FINANCE = RagActor("u-finance", "finance")
PROCUREMENT = RagActor("u-proc", "procurement")
IT = RagActor(str(IT_USER), "it", is_it=True)
TXT = "第一點 為便利營業人以電子資料申報營業稅，特訂定本要點。\n第二點 營業人得以網際網路傳輸方式申報。"


class FakeEmbedding:
    def __init__(self) -> None:
        self.calls = 0

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        task: EmbeddingTask = "document",
        title: str | None = None,
        timeout_seconds: float | None = None,
    ) -> EmbeddingBatch:
        self.calls += 1
        vectors = tuple((0.0,) * 4 for _ in texts)
        return EmbeddingBatch(vectors=vectors, model="synthetic-model", dimensions=4)


class FakeStore:
    """以 dict 模擬 RAG 表；log 記錄寫入順序。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.session = MagicMock()
        self.log: list[str] = []
        self.kbs = {
            "kb-tax": KnowledgeBase(
                "kb-tax",
                "營業稅申報",
                approval_status=APPROVAL_APPROVED,
                submitted_department_id="finance",
                knowledge_code="kb-20261030-tax00001",
            ),
            "kb-new": KnowledgeBase(
                "kb-new",
                "發票與會計法規",
                submitted_department_id="finance",
                knowledge_code="kb-20261030-new00001",
            ),
        }
        self.access = [
            DepartmentAccess("kb-tax", "finance", use_for_qa=True),
            DepartmentAccess("kb-tax", "procurement", use_for_qa=True),
            DepartmentAccess("kb-new", "finance"),
        ]
        self.files = {
            "f-txt": RagFile(
                "f-txt",
                "kb-tax",
                "作業要點",
                bucket_name="rag",
                storage_path="kb-tax/RAG_1.txt",
                original_filename="要點.txt",
                mime_type="text/plain",
            ),
            "f-pdf": RagFile(
                "f-pdf",
                "kb-tax",
                "營業稅法",
                bucket_name="rag",
                storage_path="kb-tax/RAG_2.pdf",
                original_filename="營業稅法.pdf",
                mime_type="application/pdf",
            ),
        }
        self.jobs = {
            "job-txt": RagJob("job-txt", "f-txt"),
            "job-pdf": RagJob("job-pdf", "f-pdf"),
        }
        self.chunks: dict[str, tuple[IndexedChunk, ...]] = {}
        self.usage: list[FileDepartmentUsage] = []
        self.departments = [
            DepartmentOption(d, d, d) for d in ("finance", "procurement", "it", "admin")
        ]
        r = rag_repository
        patches = {
            "open_session": lambda: self.session,
            "lock_knowledge_base": lambda db, kb_id: self.kbs.get(kb_id),
            "update_knowledge_base_review": self._update_kb,
            "lock_knowledge_base_access": lambda db, kb_id: tuple(
                a for a in self.access if a.knowledge_base_id == kb_id
            ),
            "clear_qa_for_knowledge_base": self._clear_kb,
            "lock_department_access": lambda db, dept: tuple(
                a for a in self.access if a.department_id == dept
            ),
            "clear_qa_for_department": self._clear_dept,
            "set_qa": self._set_qa,
            "lock_file": lambda db, file_id: self.files.get(file_id),
            "update_file_approval": self._update_file,
            "replace_file_content": self._replace_content,
            "insert_job": self._insert_job,
            "lock_job": lambda db, job_id: self.jobs.get(job_id),
            "update_job": self._update_job,
            "update_parse_status": self._update_parse,
            "lock_file_version": lambda db, file_id: self.files[file_id].file_version,
            "replace_chunks": self._replace_chunks,
            "list_knowledge_bases": lambda db: list(self.kbs.values()),
            "list_department_access": lambda db: tuple(self.access),
            "list_departments": lambda db: list(self.departments),
            "get_knowledge_base": lambda db, kb_id: self.kbs.get(kb_id),
            "list_knowledge_base_access": lambda db, kb_id: tuple(
                a for a in self.access if a.knowledge_base_id == kb_id
            ),
            "get_file": lambda db, file_id: self.files.get(file_id),
            "list_files": lambda db, kb_id: [
                f for f in self.files.values() if f.knowledge_base_id == kb_id
            ],
            "insert_knowledge_base": self._insert_kb,
            "rename_knowledge_base": self._rename_kb,
            "delete_knowledge_base": self._delete_kb,
            "upsert_department_access": self._upsert_access,
            "delete_department_access": self._delete_access,
            "insert_file": self._insert_file,
            "rename_file": lambda db, file_id, title: self._set_file(file_id, title=title),
            "delete_file": self._delete_file,
            "list_file_usage": lambda db, kb_id: tuple(self.usage),
            "upsert_file_usage": self._upsert_usage,
            "list_latest_jobs": lambda db, kb_id: {
                j.rag_file_id: LatestJob(j.rag_file_id, j.job_status, j.error_message)
                for j in self.jobs.values()
            },
            "find_pending_job": lambda db, file_id: next(
                (
                    j.job_id
                    for j in self.jobs.values()
                    if j.rag_file_id == file_id and j.job_status == JOB_PENDING
                ),
                None,
            ),
        }
        for name, value in patches.items():
            monkeypatch.setattr(r, name, value)

    def _update_kb(self, db: object, kb: KnowledgeBase) -> None:
        self.log.append(f"kb:{kb.knowledge_base_id}")
        self.kbs[kb.knowledge_base_id] = kb

    def _clear_kb(self, db: object, kb_id: str) -> None:
        self.log.append(f"clear-kb:{kb_id}")
        self.access = [
            replace(a, use_for_qa=False) if a.knowledge_base_id == kb_id else a for a in self.access
        ]

    def _clear_dept(self, db: object, dept: str) -> None:
        self.log.append(f"clear-dept:{dept}")
        self.access = [
            replace(a, use_for_qa=False) if a.department_id == dept else a for a in self.access
        ]

    def _set_qa(self, db: object, kb_id: str, dept: str) -> int:
        self.log.append(f"set:{kb_id}:{dept}")
        hit = 0
        for i, a in enumerate(self.access):
            if (a.knowledge_base_id, a.department_id) == (kb_id, dept) and a.can_read:
                self.access[i] = replace(a, use_for_qa=True)
                hit = 1
        return hit

    def _update_file(self, db: object, f: RagFile) -> None:
        self.files[f.rag_file_id] = f

    def _replace_content(self, db: object, f: RagFile) -> int:
        self.files[f.rag_file_id] = f
        return 1

    def _insert_job(self, db: object, job: RagJob) -> str:
        self.jobs["job-new"] = replace(job, job_id="job-new")
        return "job-new"

    def _update_job(self, db: object, job: RagJob) -> None:
        self.jobs[job.job_id] = job

    def _update_parse(self, db: object, file_id: str, version: int, status: str) -> None:
        if self.files[file_id].file_version == version:
            self.files[file_id] = replace(self.files[file_id], parse_status=status)

    def _replace_chunks(self, db: object, file_id: str, chunks: Sequence[IndexedChunk]) -> None:
        self.chunks[file_id] = tuple(chunks)

    def _insert_kb(self, db: object, code: str, name: str, user: str, dept: str) -> KnowledgeBase:
        if any(k.knowledge_name == name for k in self.kbs.values()):
            raise rag_repository.IntegrityError("insert", {}, Exception("duplicate"))
        kb = KnowledgeBase(
            "kb-created",
            name,
            submitted_by=user,
            submitted_department_id=dept,
            knowledge_code=code,
        )
        self.kbs[kb.knowledge_base_id] = kb
        return kb

    def _rename_kb(self, db: object, kb_id: str, name: str) -> None:
        if any(k.knowledge_name == name for k in self.kbs.values()):
            raise rag_repository.IntegrityError("update", {}, Exception("duplicate"))
        self.kbs[kb_id] = replace(self.kbs[kb_id], knowledge_name=name)

    def _delete_kb(self, db: object, kb_id: str) -> None:
        self.log.append(f"delete-kb:{kb_id}")
        del self.kbs[kb_id]
        self.files = {k: f for k, f in self.files.items() if f.knowledge_base_id != kb_id}

    def _upsert_access(
        self, db: object, kb_id: str, dept: str, *, can_manage: bool = False
    ) -> None:
        if not any((a.knowledge_base_id, a.department_id) == (kb_id, dept) for a in self.access):
            self.access.append(DepartmentAccess(kb_id, dept, can_manage=can_manage))

    def _delete_access(self, db: object, kb_id: str, dept: str) -> None:
        self.access = [
            a for a in self.access if (a.knowledge_base_id, a.department_id) != (kb_id, dept)
        ]

    def _insert_file(self, db: object, f: RagFile, uploaded_by: str) -> None:
        self.log.append(f"insert-file:{f.title}")
        self.files[f.rag_file_id] = f

    def _set_file(self, file_id: str, **changes: object) -> None:
        self.files[file_id] = replace(self.files[file_id], **changes)

    def _delete_file(self, db: object, file_id: str) -> None:
        self.log.append(f"delete-file:{file_id}")
        del self.files[file_id]

    def _upsert_usage(self, db: object, usage: FileDepartmentUsage) -> None:
        self.usage = [
            u
            for u in self.usage
            if (u.rag_file_id, u.department_id) != (usage.rag_file_id, usage.department_id)
        ]
        self.usage.append(usage)

    def qa(self, dept: str) -> list[str]:
        return [
            a.knowledge_base_id for a in self.access if a.department_id == dept and a.use_for_qa
        ]


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    return FakeStore(monkeypatch)


class FakeStorage:
    """記憶體 Storage；fail_put 為真時上傳失敗。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {
            "kb-20261030-tax00001/RAG_1.txt": TXT.encode(),
        }
        self.deleted: list[str] = []
        self.fail_put = False

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        if self.fail_put:
            raise StorageError("storage_unreachable")
        self.objects[key] = data

    def get(self, key: str) -> tuple[bytes, str]:
        if key not in self.objects:
            raise StorageError("storage_not_found")
        return self.objects[key], "text/plain"

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


class FakeDocumentAI:
    def __init__(self, text: str = "", error: DocumentAIError | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[float | None] = []
        self.closed = False

    def process(
        self, content: bytes, mime_type: str, *, timeout_seconds: float | None = None
    ) -> object:
        self.calls.append(timeout_seconds)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text, pages=())

    def close(self) -> None:
        self.closed = True


class EmbeddingContext(FakeEmbedding):
    def __enter__(self) -> "EmbeddingContext":
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


def _reader(content: bytes) -> object:
    return lambda bucket, path: content


# ---- 審核與越權 ----


def test_non_it_cannot_approve_and_transaction_rolls_back(store: FakeStore) -> None:
    with pytest.raises(RagRuleError) as error:
        service.approve_knowledge_base("kb-new", IT_USER, False)
    assert error.value.code == "NOT_IT_REVIEWER"
    assert store.kbs["kb-new"].approval_status == APPROVAL_PENDING
    store.session.rollback.assert_called_once()
    store.session.commit.assert_not_called()


def test_it_approves_and_commits(store: FakeStore) -> None:
    approved = service.approve_knowledge_base("kb-new", IT_USER, True)
    assert (approved.approval_status, approved.reviewed_by) == (APPROVAL_APPROVED, str(IT_USER))
    store.session.commit.assert_called_once()
    store.session.close.assert_called_once()


def test_reject_clears_every_department_qa_in_same_transaction(store: FakeStore) -> None:
    service.reject_knowledge_base("kb-tax", IT_USER, True, "內容過舊")
    assert store.qa("finance") == [] and store.qa("procurement") == []
    assert store.log == ["kb:kb-tax", "clear-kb:kb-tax"]
    store.session.commit.assert_called_once()


def test_non_it_cannot_approve_file(store: FakeStore) -> None:
    store.files["f-txt"] = replace(store.files["f-txt"], parse_status=PARSE_READY)
    with pytest.raises(RagRuleError):
        service.approve_file("f-txt", IT_USER, False)
    assert not store.files["f-txt"].is_approved


def test_approve_then_suspend_file(store: FakeStore) -> None:
    store.files["f-txt"] = replace(store.files["f-txt"], parse_status=PARSE_READY)
    assert service.approve_file("f-txt", IT_USER, True).is_approved
    suspended = service.suspend_file("f-txt", True)
    assert (suspended.is_approved, suspended.approved_by) == (False, None)


# ---- 一部門一個 ----


def test_switch_qa_source_closes_old_then_opens_new(store: FakeStore) -> None:
    store.kbs["kb-new"] = replace(store.kbs["kb-new"], approval_status=APPROVAL_APPROVED)
    service.set_qa_source("kb-new", "finance", FINANCE)
    assert store.qa("finance") == ["kb-new"]
    assert store.qa("procurement") == ["kb-tax"]
    assert store.log == ["clear-dept:finance", "set:kb-new:finance"]


def test_unapproved_kb_cannot_be_selected(store: FakeStore) -> None:
    with pytest.raises(RagRuleError) as error:
        service.set_qa_source("kb-new", "finance", FINANCE)
    assert error.value.code == "KB_NOT_APPROVED"
    assert store.log == [] and store.qa("finance") == ["kb-tax"]
    store.session.rollback.assert_called_once()


# ---- 索引工作 ----


def test_process_txt_job_writes_chunks_and_ready(store: FakeStore) -> None:
    embedding = FakeEmbedding()
    result = service.process_job("job-txt", embedding, read_object=_reader(TXT.encode()))
    assert result.outcome == INDEX_READY
    assert store.files["f-txt"].parse_status == PARSE_READY
    assert store.jobs["job-txt"].job_status == JOB_SUCCEEDED
    assert len(store.chunks["f-txt"]) == len(result.chunks) >= 1
    assert store.session.commit.call_count == 2  # 開始、寫入各一個短交易


def test_pdf_without_parser_is_failed_not_ready(store: FakeStore) -> None:
    embedding = FakeEmbedding()
    result = service.process_job("job-pdf", embedding, read_object=_reader(b"%PDF-1.7"))
    assert (result.outcome, result.error_code) == (INDEX_FAILED, ERROR_UNSUPPORTED_FORMAT)
    assert store.files["f-pdf"].parse_status == PARSE_FAILED
    assert store.jobs["job-pdf"].job_status == JOB_FAILED
    assert store.jobs["job-pdf"].error_message == ERROR_UNSUPPORTED_FORMAT
    assert "f-pdf" not in store.chunks and embedding.calls == 0


def test_blank_text_file_is_failed_not_ready(store: FakeStore) -> None:
    service.process_job("job-txt", FakeEmbedding(), read_object=_reader(b"  \n "))
    assert store.files["f-txt"].parse_status == PARSE_FAILED
    assert "f-txt" not in store.chunks


def test_version_change_during_indexing_discards_result(store: FakeStore) -> None:
    def read_and_replace(bucket: str, path: str) -> bytes:
        # 讀檔後、寫入前有人替換了內容（版本 +1）
        store.files["f-txt"] = replace(
            store.files["f-txt"], file_version=2, parse_status=PARSE_UPLOADED
        )
        return TXT.encode()

    result = service.process_job("job-txt", FakeEmbedding(), read_object=read_and_replace)
    assert (result.outcome, result.error_code) == (INDEX_STALE, ERROR_VERSION_MISMATCH)
    assert "f-txt" not in store.chunks
    assert store.files["f-txt"].parse_status == PARSE_UPLOADED  # 新版本狀態不被舊工作覆蓋
    assert store.jobs["job-txt"].job_status == JOB_FAILED


def test_storage_failure_is_retried(store: FakeStore) -> None:
    def broken(bucket: str, path: str) -> bytes:
        raise StorageError("storage_unreachable")

    result = service.process_job("job-txt", FakeEmbedding(), read_object=broken)
    assert result.error_code == ERROR_STORAGE_READ
    assert store.jobs["job-txt"].job_status == JOB_PENDING
    assert store.files["f-txt"].parse_status == PARSE_UPLOADED


def test_missing_or_locked_job(store: FakeStore) -> None:
    with pytest.raises(RagRuleError):
        service.process_job("job-none", FakeEmbedding(), read_object=_reader(b"x"))


def test_content_replacement_revokes_approval_and_queues_job(
    store: FakeStore, storage: FakeStorage
) -> None:
    store.files["f-txt"] = replace(
        store.files["f-txt"],
        parse_status=PARSE_READY,
        is_approved=True,
        approved_by="it",
        storage_path="kb-20261030-tax00001/RAG_1.txt",
    )
    updated, job_id = service.replace_file_content(
        "kb-tax", "f-txt", "要點.txt", "第一點 新內容".encode(), FINANCE, storage
    )
    assert (updated.file_version, updated.is_approved, updated.parse_status) == (
        2,
        False,
        PARSE_UPLOADED,
    )
    assert store.jobs[job_id].job_type == "reindex"
    assert updated.storage_path in storage.objects
    assert storage.deleted == ["kb-20261030-tax00001/RAG_1.txt"]  # 提交後才刪舊物件


def test_content_replacement_by_other_department_removes_new_object(
    store: FakeStore, storage: FakeStorage
) -> None:
    with pytest.raises(RagRuleError) as error:
        service.replace_file_content("kb-tax", "f-txt", "要點.txt", b"x", PROCUREMENT, storage)
    assert error.value.code == "NOT_MANAGER"
    assert store.files["f-txt"].file_version == 1
    assert len(storage.deleted) == 1 and storage.deleted[0] not in storage.objects


def test_database_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> object:
        raise rag_repository.SQLAlchemyError("down")

    monkeypatch.setattr(rag_repository, "open_session", broken)
    with pytest.raises(service.RagUnavailableError):
        service.load_overview()


# ---- 知識庫新增、改名、刪除 ----


def test_create_knowledge_base_is_pending_and_assigns_department(store: FakeStore) -> None:
    kb = service.create_knowledge_base("  採購法規  ", IT)
    assert (kb.knowledge_name, kb.approval_status) == ("採購法規", APPROVAL_PENDING)
    assert re.fullmatch(r"kb-\d{8}-[0-9a-f]{8}", kb.knowledge_code or "")
    assert any(
        a.knowledge_base_id == kb.knowledge_base_id and a.department_id == "it" and a.can_manage
        for a in store.access
    )


def test_duplicate_knowledge_base_name_rolls_back(store: FakeStore) -> None:
    with pytest.raises(RagRuleError) as error:
        service.create_knowledge_base("營業稅申報", FINANCE)
    assert error.value.code == "DUPLICATE_NAME"
    store.session.rollback.assert_called_once()


def test_other_department_cannot_rename_or_delete(store: FakeStore, storage: FakeStorage) -> None:
    with pytest.raises(RagRuleError):
        service.rename_knowledge_base("kb-tax", "改名", PROCUREMENT)
    with pytest.raises(RagRuleError):
        service.delete_knowledge_base("kb-tax", PROCUREMENT, storage)
    assert store.kbs["kb-tax"].knowledge_name == "營業稅申報" and storage.deleted == []


def test_can_manage_department_may_rename(store: FakeStore) -> None:
    store.access.append(DepartmentAccess("kb-tax", "admin", can_manage=True))
    service.rename_knowledge_base("kb-tax", "營業稅申報（新）", RagActor("u-admin", "admin"))
    assert store.kbs["kb-tax"].knowledge_name == "營業稅申報（新）"


def test_delete_knowledge_base_removes_objects_after_commit(
    store: FakeStore, storage: FakeStorage
) -> None:
    service.delete_knowledge_base("kb-tax", IT, storage)
    assert "kb-tax" not in store.kbs
    assert storage.deleted == ["kb-tax/RAG_1.txt", "kb-tax/RAG_2.pdf"]
    store.session.commit.assert_called_once()


def test_assignment_toggle_and_unknown_department(store: FakeStore) -> None:
    rows = service.set_department_assignment("kb-new", "procurement", True, FINANCE)
    assert {r.department_id for r in rows} == {"finance", "procurement"}
    rows = service.set_department_assignment("kb-new", "procurement", False, FINANCE)
    assert {r.department_id for r in rows} == {"finance"}
    with pytest.raises(RagRuleError) as error:
        service.set_department_assignment("kb-new", "ghost", True, FINANCE)
    assert error.value.code == "NOT_FOUND"


# ---- 上傳 ----


def test_upload_stores_object_then_inserts_file_usage_and_job(
    store: FakeStore, storage: FakeStorage
) -> None:
    outcomes = service.upload_documents(
        "kb-tax",
        [service.UploadItem("作業要點.pdf", b"%PDF-1.7", "營業稅作業要點")],
        FINANCE,
        storage,
    )
    assert [o.ok for o in outcomes] == [True]
    created = next(f for f in store.files.values() if f.title == "營業稅作業要點")
    assert (created.is_approved, created.parse_status, created.bucket_name) == (
        False,
        PARSE_UPLOADED,
        "rag",
    )
    assert created.owner_department_id == "finance"
    assert re.fullmatch(
        rf"kb-20261030-tax00001/RAG_\d{{14}}_{created.rag_file_id}\.pdf", created.storage_path or ""
    )
    assert created.storage_path in storage.objects
    assert store.usage == [FileDepartmentUsage(created.rag_file_id, "finance", True, "u-finance")]
    assert any(j.rag_file_id == created.rag_file_id for j in store.jobs.values())


def test_upload_rejects_bad_format_and_large_file(store: FakeStore, storage: FakeStorage) -> None:
    big = b"x" * (service.MAX_UPLOAD_BYTES + 1)
    outcomes = service.upload_documents(
        "kb-tax",
        [service.UploadItem("a.exe", b"MZ"), service.UploadItem("b.txt", big)],
        FINANCE,
        storage,
    )
    assert [o.ok for o in outcomes] == [False, False]
    assert len(storage.objects) == 1 and "insert-file" not in " ".join(store.log)


def test_upload_requires_assigned_department(store: FakeStore, storage: FakeStorage) -> None:
    with pytest.raises(RagRuleError) as error:
        service.upload_documents(
            "kb-new", [service.UploadItem("a.txt", b"x")], PROCUREMENT, storage
        )
    assert error.value.code == "NOT_ASSIGNED"


def test_upload_db_failure_removes_stored_object(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(db: object, f: RagFile, uploaded_by: str) -> None:
        raise rag_repository.SQLAlchemyError("down")

    monkeypatch.setattr(rag_repository, "insert_file", broken)
    (outcome,) = service.upload_documents(
        "kb-tax", [service.UploadItem("a.txt", b"x")], FINANCE, storage
    )
    assert not outcome.ok
    assert len(storage.deleted) == 1 and storage.deleted[0] not in storage.objects


def test_storage_key_uses_taiwan_time() -> None:
    key = service.storage_key("kb-x", "id-1", "md", datetime(2026, 10, 30, 16, 5, 9, tzinfo=UTC))
    assert key == "kb-x/RAG_20261031000509_id-1.md"


# ---- 文件選用、刪除 ----


def test_department_sets_own_document_usage_only(store: FakeStore) -> None:
    store.access.append(DepartmentAccess("kb-tax", "admin"))
    service.set_file_usage("kb-tax", "f-txt", "admin", False, RagActor("u-admin", "admin"))
    assert store.usage[-1].use_document is False
    with pytest.raises(RagRuleError):
        service.set_file_usage("kb-tax", "f-txt", "finance", False, RagActor("u-admin", "admin"))


def test_delete_file_removes_object(store: FakeStore, storage: FakeStorage) -> None:
    service.delete_file("kb-tax", "f-txt", FINANCE, storage)
    assert "f-txt" not in store.files and storage.deleted == ["kb-tax/RAG_1.txt"]


def test_file_from_other_knowledge_base_is_not_found(store: FakeStore) -> None:
    with pytest.raises(RagRuleError) as error:
        service.rename_file("kb-new", "f-txt", "x", FINANCE)
    assert error.value.code == "NOT_FOUND"


# ---- PDF 擷取與手動索引 ----


def test_pdf_is_extracted_by_document_ai(store: FakeStore) -> None:
    pdf = FakeDocumentAI(text="第一點 營業人應依規定申報。")
    extractor = indexing.DocumentTextExtractor(lambda: pdf)
    result = service.process_job(
        "job-pdf", FakeEmbedding(), extractor, read_object=_reader(b"%PDF-1.7")
    )
    assert result.outcome == INDEX_READY
    assert store.files["f-pdf"].parse_status == PARSE_READY
    assert pdf.calls == [indexing.PDF_TIMEOUT_SECONDS]


def test_scanned_pdf_without_text_is_failed(store: FakeStore) -> None:
    extractor = indexing.DocumentTextExtractor(lambda: FakeDocumentAI(text="   "))
    service.process_job("job-pdf", FakeEmbedding(), extractor, read_object=_reader(b"%PDF"))
    assert store.files["f-pdf"].parse_status == PARSE_FAILED
    assert "f-pdf" not in store.chunks


def test_pdf_over_page_limit_is_failed_with_fixed_code(store: FakeStore) -> None:
    error = DocumentAIError("document_ai_invalid_input", "INVALID_ARGUMENT/PAGE_LIMIT_EXCEEDED")
    extractor = indexing.DocumentTextExtractor(lambda: FakeDocumentAI(error=error))
    result = service.process_job("job-pdf", FakeEmbedding(), extractor, read_object=_reader(b"%"))
    assert result.error_code == ERROR_PDF_PAGE_LIMIT
    assert store.jobs["job-pdf"].error_message == ERROR_PDF_PAGE_LIMIT
    assert store.files["f-pdf"].parse_status == PARSE_FAILED


def test_document_ai_timeout_is_retried(store: FakeStore) -> None:
    extractor = indexing.DocumentTextExtractor(
        lambda: FakeDocumentAI(error=DocumentAIError("document_ai_timeout"))
    )
    service.process_job("job-pdf", FakeEmbedding(), extractor, read_object=_reader(b"%"))
    assert store.jobs["job-pdf"].job_status == JOB_PENDING
    assert store.files["f-pdf"].parse_status == PARSE_UPLOADED


def test_index_document_runs_pending_job_and_closes_clients(store: FakeStore) -> None:
    pdf = FakeDocumentAI(text="第一點 內容")
    result = service.index_document(
        "kb-tax",
        "f-pdf",
        FINANCE,
        embedding_factory=EmbeddingContext,
        pdf_client_factory=lambda: pdf,
        read_object=_reader(b"%PDF"),
    )
    assert result.outcome == INDEX_READY and pdf.closed


def test_index_document_requeues_failed_file(store: FakeStore) -> None:
    store.jobs = {"job-old": RagJob("job-old", "f-txt", job_status=JOB_FAILED)}
    store.files["f-txt"] = replace(store.files["f-txt"], parse_status=PARSE_FAILED)
    result = service.index_document(
        "kb-tax",
        "f-txt",
        FINANCE,
        embedding_factory=EmbeddingContext,
        read_object=_reader(TXT.encode()),
    )
    assert result.outcome == INDEX_READY
    assert store.jobs["job-new"].job_type == "reindex"


def test_index_document_rejects_ready_file_and_other_department(store: FakeStore) -> None:
    store.jobs = {}
    store.files["f-txt"] = replace(store.files["f-txt"], parse_status=PARSE_READY)
    with pytest.raises(RagRuleError) as ready:
        service.index_document("kb-tax", "f-txt", FINANCE, embedding_factory=EmbeddingContext)
    with pytest.raises(RagRuleError) as other:
        service.index_document("kb-tax", "f-pdf", PROCUREMENT, embedding_factory=EmbeddingContext)
    assert (ready.value.code, other.value.code) == ("ALREADY_INDEXED", "NOT_MANAGER")


# ---- 問答選用範圍 ----


def test_qa_source_only_for_own_department_unless_it(store: FakeStore) -> None:
    with pytest.raises(RagRuleError) as error:
        service.clear_qa_source("procurement", FINANCE)
    assert error.value.code == "OTHER_DEPARTMENT"
    service.clear_qa_source("procurement", IT)
    assert store.qa("procurement") == []
