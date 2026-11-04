"""
模組名稱：tests.unit.test_rag_admin_service
功能說明：RAG 管理交易測試——越權回滾、退回清除選用、部門多選（先清除再設定）、處理索引工作
          （成功、掃描檔／PDF 不可 ready、PDF 超過 15 頁、版本不符丟棄、讀檔失敗重試）、上傳與
          Storage 路徑、內容替換、管理範圍、刪除、DB 無法使用。
注意事項：
    - 以記憶體替身取代 rag_repository 的函式、Session、Storage 與 Document AI，
      不連正式 DB／Storage／Google。
    - 向量用戶端使用 docs/RAG_DEVELOPMENT.md 的 FakeEmbedding 寫法。
"""

import hashlib
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
                file_count=1,
            ),
            "kb-new": KnowledgeBase(
                "kb-new",
                "發票與會計法規",
                submitted_department_id="finance",
                knowledge_code="kb-20261030-new00001",
                file_count=1,
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
                "作業要點",
                bucket_name="rag",
                storage_path="kb-tax/RAG_1.txt",
                original_filename="要點.txt",
                mime_type="text/plain",
            ),
            "f-pdf": RagFile(
                "f-pdf",
                "營業稅法",
                bucket_name="rag",
                storage_path="kb-tax/RAG_2.pdf",
                original_filename="營業稅法.pdf",
                mime_type="application/pdf",
            ),
        }
        self.links = {("kb-tax", "f-txt"), ("kb-tax", "f-pdf"), ("kb-new", "f-pdf")}
        self.files = {
            key: replace(
                f,
                owner_department_id="finance",
                content_sha256=hashlib.sha256(("legacy:" + key).encode()).hexdigest(),
            )
            for key, f in self.files.items()
        }
        for key in self.kbs:
            self.kbs[key] = replace(self.kbs[key], file_count=sum(k == key for k, _ in self.links))
        self.jobs = {
            "job-txt": RagJob("job-txt", "f-txt"),
            "job-pdf": RagJob("job-pdf", "f-pdf"),
        }
        self.chunks: dict[str, tuple[IndexedChunk, ...]] = {}
        self.departments = [
            DepartmentOption(d, d, d) for d in ("finance", "procurement", "it", "admin")
        ]
        self.inactive_departments: list[DepartmentOption] = []
        r = rag_repository
        patches = {
            "open_session": self._open,
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
            "get_job": lambda db, job_id: self.jobs.get(job_id),
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
                f for f in self.files.values() if (kb_id, f.rag_file_id) in self.links
            ],
            "list_library_files": lambda db: list(self.files.values()),
            "list_unhashed_files": lambda db: [
                f for f in self.files.values() if not f.content_sha256
            ],
            "list_all_latest_jobs": lambda db: {},
            "list_all_file_references": lambda db: {
                file_id: tuple(
                    k for k in self.kbs.values() if (k.knowledge_base_id, file_id) in self.links
                )
                for _kb_id, file_id in self.links
            },
            "list_file_references": lambda db, file_id: tuple(
                k for k in self.kbs.values() if (k.knowledge_base_id, file_id) in self.links
            ),
            "insert_file_link": self._insert_link,
            "delete_file_link": self._delete_link,
            "get_department": lambda db, dept: next(
                (d for d in self.departments if d.department_id == dept), None
            ),
            "get_department_code": lambda db, dept: next(
                (
                    d.department_code
                    for d in (*self.departments, *self.inactive_departments)
                    if d.department_id == dept
                ),
                None,
            ),
            "lock_content_hash": lambda db, digest: None,
            "find_duplicate_file": lambda db, digest, exclude: next(
                (
                    f
                    for f in self.files.values()
                    if f.content_sha256 == digest and f.rag_file_id != exclude
                ),
                None,
            ),
            "set_legacy_hash": self._set_legacy_hash,
            "insert_knowledge_base": self._insert_kb,
            "rename_knowledge_base": self._rename_kb,
            "delete_knowledge_base": self._delete_kb,
            "upsert_department_access": self._upsert_access,
            "delete_department_access": self._delete_access,
            "insert_file": self._insert_file,
            "rename_file": lambda db, file_id, title: self._set_file(file_id, title=title),
            "delete_file": self._delete_file,
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
        self._snapshot = (
            list(self.access),
            dict(self.kbs),
            dict(self.files),
            set(self.links),
            dict(self.jobs),
            dict(self.chunks),
        )
        self.session.commit.side_effect = self._commit_access
        self.session.rollback.side_effect = self._rollback_access

    def _open(self) -> MagicMock:
        self._snapshot = (
            list(self.access),
            dict(self.kbs),
            dict(self.files),
            set(self.links),
            dict(self.jobs),
            dict(self.chunks),
        )
        return self.session

    def _commit_access(self) -> None:
        self._snapshot = (
            list(self.access),
            dict(self.kbs),
            dict(self.files),
            set(self.links),
            dict(self.jobs),
            dict(self.chunks),
        )

    def _rollback_access(self) -> None:
        access, kbs, files, links, jobs, chunks = self._snapshot
        self.access, self.kbs, self.files, self.links, self.jobs, self.chunks = (
            list(access),
            dict(kbs),
            dict(files),
            set(links),
            dict(jobs),
            dict(chunks),
        )

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
            file_count=0,
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
        self.links = {(k, f) for k, f in self.links if k != kb_id}
        self.access = [a for a in self.access if a.knowledge_base_id != kb_id]

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

    def _insert_link(self, db: object, kb_id: str, file_id: str, actor: str) -> None:
        self.links.add((kb_id, file_id))
        self.kbs[kb_id] = replace(
            self.kbs[kb_id], file_count=sum(k == kb_id for k, _ in self.links)
        )

    def _delete_link(self, db: object, kb_id: str, file_id: str) -> None:
        self.links.discard((kb_id, file_id))
        count = sum(k == kb_id for k, _ in self.links)
        self.kbs[kb_id] = replace(self.kbs[kb_id], file_count=count)
        if count == 0:
            self.access = [
                replace(a, use_for_qa=False) if a.knowledge_base_id == kb_id else a
                for a in self.access
            ]

    def _set_legacy_hash(self, db: object, file_id: str, version: int, digest: str) -> None:
        f = self.files[file_id]
        if f.file_version == version and f.content_sha256 is None:
            self.files[file_id] = replace(f, content_sha256=digest)

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
        self.timeouts: list[float] = []

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        if self.fail_put:
            raise StorageError("storage_unreachable")
        self.objects[key] = data

    def get(self, key: str, *, timeout_seconds: float = 30.0) -> tuple[bytes, str]:
        self.timeouts.append(timeout_seconds)
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
        self,
        content: bytes,
        mime_type: str,
        *,
        timeout_seconds: float | None = None,
        options: object = None,
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


# ---- 部門多選 ----


def test_switch_qa_source_closes_old_then_opens_new(store: FakeStore) -> None:
    store.kbs["kb-new"] = replace(store.kbs["kb-new"], approval_status=APPROVAL_APPROVED)
    service.set_qa_sources(["kb-new"], "finance", FINANCE)
    assert store.qa("finance") == ["kb-new"]
    assert store.qa("procurement") == ["kb-tax"]
    assert store.log == ["clear-dept:finance", "set:kb-new:finance"]


def test_unapproved_kb_cannot_be_selected(store: FakeStore) -> None:
    with pytest.raises(RagRuleError) as error:
        service.set_qa_sources(["kb-new"], "finance", FINANCE)
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
    store.kbs["kb-tax"] = replace(store.kbs["kb-tax"], approval_status=APPROVAL_PENDING)
    updated, job_id = service.replace_file_content(
        "f-txt", "要點.txt", "第一點 新內容".encode(), FINANCE, storage
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
        service.replace_file_content("f-txt", "要點.txt", b"x", PROCUREMENT, storage)
    assert error.value.code == "NOT_FILE_OWNER"
    assert store.files["f-txt"].file_version == 1
    assert storage.deleted == []


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
        service.delete_knowledge_base("kb-tax", PROCUREMENT)
    assert store.kbs["kb-tax"].knowledge_name == "營業稅申報" and storage.deleted == []


def test_can_manage_department_may_rename(store: FakeStore) -> None:
    store.access.append(DepartmentAccess("kb-tax", "admin", can_manage=True))
    service.rename_knowledge_base("kb-tax", "營業稅申報（新）", RagActor("u-admin", "admin"))
    assert store.kbs["kb-tax"].knowledge_name == "營業稅申報（新）"


def test_delete_knowledge_base_keeps_files_and_objects(
    store: FakeStore, storage: FakeStorage
) -> None:
    service.delete_knowledge_base("kb-tax", IT)
    assert "kb-tax" not in store.kbs
    assert storage.deleted == []
    assert set(store.files) == {"f-txt", "f-pdf"}
    assert ("kb-new", "f-pdf") in store.links
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


def test_upload_stores_object_then_inserts_file_and_job(
    store: FakeStore, storage: FakeStorage
) -> None:
    outcomes = service.upload_documents(
        "kb-tax",
        [
            service.UploadItem(
                "作業要點.pdf",
                b"%PDF-1.7",
                "營業稅作業要點",
                source_type="company_policy",
                effective_date_status="unknown",
            )
        ],
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
        rf"finance/RAG_\d{{14}}_{created.rag_file_id}\.pdf", created.storage_path or ""
    )
    assert created.storage_path in storage.objects
    assert any(j.rag_file_id == created.rag_file_id for j in store.jobs.values())


def test_upload_rejects_bad_format_and_large_file(store: FakeStore, storage: FakeStorage) -> None:
    big = b"x" * (service.MAX_UPLOAD_BYTES + 1)
    outcomes = service.upload_documents(
        "kb-tax",
        [
            service.UploadItem(
                "a.exe", b"MZ", source_type="company_policy", effective_date_status="unknown"
            ),
            service.UploadItem(
                "b.txt", big, source_type="company_policy", effective_date_status="unknown"
            ),
        ],
        FINANCE,
        storage,
    )
    assert [o.ok for o in outcomes] == [False, False]
    assert len(storage.objects) == 1 and "insert-file" not in " ".join(store.log)


def test_upload_requires_assigned_department(store: FakeStore, storage: FakeStorage) -> None:
    with pytest.raises(RagRuleError) as error:
        service.upload_documents(
            "kb-new",
            [
                service.UploadItem(
                    "a.txt", b"x", source_type="company_policy", effective_date_status="unknown"
                )
            ],
            PROCUREMENT,
            storage,
        )
    assert error.value.code == "NOT_MANAGER"


def test_upload_db_failure_removes_stored_object(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(db: object, f: RagFile, uploaded_by: str) -> None:
        raise rag_repository.SQLAlchemyError("down")

    monkeypatch.setattr(rag_repository, "insert_file", broken)
    (outcome,) = service.upload_documents(
        "kb-tax",
        [
            service.UploadItem(
                "a.txt", b"x", source_type="company_policy", effective_date_status="unknown"
            )
        ],
        FINANCE,
        storage,
    )
    assert not outcome.ok
    assert len(storage.deleted) == 1 and storage.deleted[0] not in storage.objects


def test_storage_key_uses_taiwan_time() -> None:
    key = service.storage_key("kb-x", "id-1", "md", datetime(2026, 10, 30, 16, 5, 9, tzinfo=UTC))
    assert key == "kb-x/RAG_20261031000509_id-1.md"


# ---- 刪除 ----


def test_delete_file_removes_object(store: FakeStore, storage: FakeStorage) -> None:
    store.links = {(k, f) for k, f in store.links if f != "f-txt"}
    service.delete_file("f-txt", FINANCE, storage)
    assert "f-txt" not in store.files and storage.deleted == ["kb-tax/RAG_1.txt"]


def test_other_department_cannot_rename_shared_file(store: FakeStore) -> None:
    with pytest.raises(RagRuleError) as error:
        service.rename_file("f-txt", "x", PROCUREMENT)
    assert error.value.code == "NOT_FILE_OWNER"


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
        "f-pdf",
        IT,
        embedding_factory=EmbeddingContext,
        pdf_client_factory=lambda: pdf,
        read_object=_reader(b"%PDF"),
    )
    assert result.outcome == INDEX_READY and pdf.closed


def test_index_document_requeues_failed_file(store: FakeStore) -> None:
    store.jobs = {"job-old": RagJob("job-old", "f-txt", job_status=JOB_FAILED)}
    store.files["f-txt"] = replace(store.files["f-txt"], parse_status=PARSE_FAILED)
    result = service.index_document(
        "f-txt",
        IT,
        embedding_factory=EmbeddingContext,
        read_object=_reader(TXT.encode()),
    )
    assert result.outcome == INDEX_READY
    assert store.jobs["job-new"].job_type == "reindex"


def test_index_document_rejects_ready_file_and_non_it(store: FakeStore) -> None:
    store.jobs = {}
    store.files["f-txt"] = replace(store.files["f-txt"], parse_status=PARSE_READY)
    with pytest.raises(RagRuleError) as ready:
        service.index_document("f-txt", IT, embedding_factory=EmbeddingContext)
    with pytest.raises(RagRuleError) as uploader:
        service.index_document("f-pdf", FINANCE, embedding_factory=EmbeddingContext)
    assert (ready.value.code, uploader.value.code) == ("ALREADY_INDEXED", "NOT_IT_REVIEWER")


# ---- 問答選用範圍 ----


def test_qa_source_only_for_own_department_unless_it(store: FakeStore) -> None:
    with pytest.raises(RagRuleError) as error:
        service.set_qa_sources([], "procurement", FINANCE)
    assert error.value.code == "OTHER_DEPARTMENT"
    service.set_qa_sources([], "procurement", IT)
    assert store.qa("procurement") == []


def test_queue_reindex_is_it_only(store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    queued: list[str] = []
    monkeypatch.setattr(rag_repository, "queue_reindex", lambda db, kb: queued.append(kb) or 2)

    with pytest.raises(RagRuleError):
        service.queue_reindex("kb-tax", FINANCE)
    assert service.queue_reindex("kb-tax", IT) == 2 and queued == ["kb-tax"]


def test_multiple_sources_replace_all_then_empty_clears(store: FakeStore) -> None:
    store.kbs["kb-new"] = replace(store.kbs["kb-new"], approval_status=APPROVAL_APPROVED)
    updated = service.set_qa_sources(["kb-tax", "kb-new", "kb-tax"], "finance", FINANCE)
    assert set(store.qa("finance")) == {"kb-tax", "kb-new"}
    assert {r.knowledge_base_id for r in updated if r.use_for_qa} == {"kb-tax", "kb-new"}
    assert store.log == ["clear-dept:finance", "set:kb-new:finance", "set:kb-tax:finance"]
    service.set_qa_sources(["kb-new"], "finance", FINANCE)
    assert store.qa("finance") == ["kb-new"]
    service.set_qa_sources([], "finance", FINANCE)
    assert store.qa("finance") == []
    assert store.qa("procurement") == ["kb-tax"]
    assert store.session.commit.call_count == 3


@pytest.mark.parametrize("invalid", ["pending", "inactive", "unreadable", "unassigned", "missing"])
def test_one_invalid_selection_leaves_original_set(store: FakeStore, invalid: str) -> None:
    store.kbs["kb-new"] = replace(store.kbs["kb-new"], approval_status=APPROVAL_APPROVED)
    if invalid == "pending":
        store.kbs["kb-tax"] = replace(store.kbs["kb-tax"], approval_status=APPROVAL_PENDING)
    elif invalid == "inactive":
        store.kbs["kb-tax"] = replace(store.kbs["kb-tax"], is_active=False)
    elif invalid == "unreadable":
        store.access = [
            replace(a, can_read=False) if a.knowledge_base_id == "kb-tax" else a
            for a in store.access
        ]
    elif invalid == "unassigned":
        store.access = [
            a
            for a in store.access
            if (a.knowledge_base_id, a.department_id) != ("kb-tax", "finance")
        ]
    else:
        del store.kbs["kb-tax"]
    before = list(store.access)
    with pytest.raises(RagRuleError) as error:
        service.set_qa_sources(["kb-new", "kb-tax"], "finance", FINANCE)
    expected = "KB_NOT_APPROVED" if invalid in {"pending", "inactive"} else "NO_READ_ACCESS"
    assert error.value.code == ("NOT_FOUND" if invalid == "missing" else expected)
    assert store.access == before and store.log == []
    store.session.rollback.assert_called_once()
    store.session.commit.assert_not_called()


@pytest.mark.parametrize("failure", ["rowcount", "database"])
def test_failure_after_first_write_rolls_back_entire_selection(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    store.kbs["kb-new"] = replace(store.kbs["kb-new"], approval_status=APPROVAL_APPROVED)
    before = list(store.access)

    def fail_second(db: object, kb_id: str, dept: str) -> int:
        if kb_id == "kb-tax":
            if failure == "database":
                raise rag_repository.SQLAlchemyError("synthetic write failure")
            return 0
        return store._set_qa(db, kb_id, dept)

    monkeypatch.setattr(rag_repository, "set_qa", fail_second)
    with pytest.raises(service.RagUnavailableError if failure == "database" else RagRuleError):
        service.set_qa_sources(["kb-tax", "kb-new"], "finance", FINANCE)
    assert store.log == ["clear-dept:finance", "set:kb-new:finance"]
    assert store.access == before
    store.session.rollback.assert_called_once()
    store.session.commit.assert_not_called()


def test_ids_deduplicated_and_locked_in_sorted_order(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.kbs["kb-new"] = replace(store.kbs["kb-new"], approval_status=APPROVAL_APPROVED)
    locks: list[str] = []

    def lock_kb(db: object, kb_id: str) -> KnowledgeBase:
        locks.append(kb_id)
        return store.kbs[kb_id]

    def lock_department(db: object, dept: str) -> tuple[DepartmentAccess, ...]:
        locks.append(dept)
        return tuple(a for a in store.access if a.department_id == dept)

    monkeypatch.setattr(rag_repository, "lock_knowledge_base", lock_kb)
    monkeypatch.setattr(rag_repository, "lock_department_access", lock_department)
    service.set_qa_sources(["kb-tax", "kb-new", "kb-tax"], "finance", FINANCE)
    assert locks == ["kb-new", "kb-tax", "finance"]
    assert store.log.count("set:kb-tax:finance") == 1


def test_other_department_cannot_set_sources_but_it_can(store: FakeStore) -> None:
    with pytest.raises(RagRuleError) as error:
        service.set_qa_sources(["kb-tax"], "procurement", FINANCE)
    assert error.value.code == "OTHER_DEPARTMENT"
    store.session.commit.assert_not_called()
    service.set_qa_sources(["kb-tax"], "procurement", IT)
    assert store.qa("procurement") == ["kb-tax"]


def test_rejection_preserves_other_selected_knowledge_bases(store: FakeStore) -> None:
    store.kbs["kb-new"] = replace(store.kbs["kb-new"], approval_status=APPROVAL_APPROVED)
    service.set_qa_sources(["kb-tax", "kb-new"], "finance", FINANCE)
    service.reject_knowledge_base("kb-tax", IT_USER, True, "需更新")
    assert store.qa("finance") == ["kb-new"]
    assert store.qa("procurement") == []


def test_selected_external_knowledge_bases_retrieve_all_eligible_documents_without_file_usage(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.schemas.rag import RetrievalCandidate
    from app.services import rag_retrieval_service as retrieval

    for kb_id in store.kbs:
        store.kbs[kb_id] = replace(
            store.kbs[kb_id],
            approval_status=APPROVAL_APPROVED,
            submitted_department_id="procurement",
        )
    store.files = {
        f"f-{n}": RagFile(
            f"f-{n}",
            f"文件{n}",
            owner_department_id="procurement",
            is_approved=n != 3,
            parse_status=PARSE_UPLOADED if n == 4 else PARSE_READY,
        )
        for n in range(1, 5)
    }
    store.links = {(kb_id, file_id) for kb_id in store.kbs for file_id in store.files}
    for kb_id in store.kbs:
        store.kbs[kb_id] = replace(store.kbs[kb_id], file_count=4)
    service.set_qa_sources(["kb-new", "kb-tax"], "finance", FINANCE)

    def candidates(
        db: object, vector: Sequence[float], model: str, dept: UUID, top_k: int
    ) -> list[RetrievalCandidate]:
        result = []
        for f in store.files.values():
            kbs = [
                k
                for k in store.kbs.values()
                if (k.knowledge_base_id, f.rag_file_id) in store.links
                and k.knowledge_base_id in store.qa("finance")
            ]
            if kbs:
                result.append(
                    RetrievalCandidate(
                        f.rag_file_id,
                        f.rag_file_id,
                        0,
                        f.title,
                        0.1,
                        model,
                        f.title,
                        1,
                        tuple(k.knowledge_base_id for k in kbs),
                        tuple(k.knowledge_name for k in kbs),
                        "approved",
                        True,
                        True,
                        f.is_approved,
                        f.parse_status,
                        file_count=4,
                    )
                )
        return result

    monkeypatch.setattr(rag_repository, "has_qa_access", lambda db, user, dept: True)
    monkeypatch.setattr(rag_repository, "search_chunks", candidates)
    result = retrieval.search(
        "法規內容？", IT_USER, IT_USER, 8, FakeEmbedding(), open_session=store._open
    )
    assert [h.rag_file_id for h in result.hits] == ["f-1", "f-2"]
    assert all(
        set(h.knowledge_names) == {k.knowledge_name for k in store.kbs.values()}
        for h in result.hits
    )
    service.set_qa_sources(["kb-new"], "finance", FINANCE)
    result = retrieval.search(
        "法規內容？", IT_USER, IT_USER, 8, FakeEmbedding(), open_session=store._open
    )
    assert all(h.knowledge_names == (store.kbs["kb-new"].knowledge_name,) for h in result.hits)


# ---- v1.8 獨立檔案與組合 ----


def test_delete_kb_preserves_shared_file_and_never_constructs_storage(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = MagicMock(side_effect=AssertionError("知識庫刪除不得存取 Storage"))
    monkeypatch.setattr(service, "rag_storage", factory)
    store.chunks["f-pdf"] = (IndexedChunk(0, "條文", (0.0,), "m"),)
    before_jobs = dict(store.jobs)
    service.delete_knowledge_base("kb-tax", IT)
    assert "f-pdf" in store.files and "f-txt" in store.files
    assert store.chunks["f-pdf"] and store.jobs == before_jobs
    assert ("kb-new", "f-pdf") in store.links
    factory.assert_not_called()


def test_referenced_file_delete_lists_all_knowledge_bases(
    store: FakeStore, storage: FakeStorage
) -> None:
    with pytest.raises(RagRuleError) as error:
        service.delete_file("f-pdf", FINANCE, storage)
    assert error.value.code == "FILE_REFERENCED"
    assert all(k.knowledge_name in error.value.message for k in store.kbs.values())
    assert "f-pdf" in store.files and storage.deleted == []
    store.session.commit.assert_not_called()


def test_replacement_of_approved_kb_file_fails_before_storage(
    store: FakeStore, storage: FakeStorage
) -> None:
    before = store.files["f-txt"]
    with pytest.raises(RagRuleError) as error:
        service.replace_file_content("f-txt", "new.txt", b"new", FINANCE, storage)
    assert error.value.code == "FILE_IN_APPROVED_KB"
    assert "上傳新檔案" in error.value.message
    assert store.files["f-txt"] == before and not storage.deleted
    assert len(storage.objects) == 1


def test_kb_manager_may_link_but_cannot_change_shared_file(
    store: FakeStore, storage: FakeStorage
) -> None:
    manager = RagActor("manager", "admin")
    store.access.append(DepartmentAccess("kb-new", "admin", can_manage=True))
    store.files["f-txt"] = replace(store.files["f-txt"], is_approved=True, parse_status=PARSE_READY)
    before = dict(store.files)
    service.add_files("kb-new", ["f-txt", "f-txt"], manager)
    assert ("kb-new", "f-txt") in store.links and store.kbs["kb-new"].file_count == 2
    for operation in [
        lambda: service.rename_file("f-txt", "x", manager),
        lambda: service.delete_file("f-txt", manager, storage),
        lambda: service.replace_file_content("f-txt", "x.txt", b"x", manager, storage),
    ]:
        with pytest.raises(RagRuleError) as error:
            operation()
        assert error.value.code == "NOT_FILE_OWNER"
    service.remove_file("kb-new", "f-txt", manager, 2)
    assert ("kb-new", "f-txt") not in store.links and store.files == before
    assert store.chunks == {} and storage.deleted == []


def test_link_validation_is_atomic(store: FakeStore) -> None:
    store.files["f-txt"] = replace(store.files["f-txt"], is_approved=True)
    before = set(store.links)
    with pytest.raises(RagRuleError) as error:
        service.add_files("kb-new", ["f-txt", "f-pdf"], FINANCE)
    assert error.value.code == "FILE_NOT_APPROVED" and store.links == before


def test_removing_last_link_clears_department_selection_without_deleting_file(
    store: FakeStore,
) -> None:
    store.kbs["kb-new"] = replace(store.kbs["kb-new"], approval_status=APPROVAL_APPROVED)
    service.set_qa_sources(["kb-new", "kb-tax"], "finance", FINANCE)
    service.remove_file("kb-new", "f-pdf", FINANCE, 1)
    assert store.kbs["kb-new"].file_count == 0
    assert store.qa("finance") == ["kb-tax"] and "f-pdf" in store.files
    store.files["f-pdf"] = replace(store.files["f-pdf"], is_approved=True)
    service.add_files("kb-new", ["f-pdf"], FINANCE)
    assert store.qa("finance") == ["kb-tax"]  # Trigger 不恢復舊選用


def test_remove_link_revalidates_impact(store: FakeStore) -> None:
    before = set(store.links)
    with pytest.raises(RagRuleError) as error:
        service.remove_file("kb-tax", "f-txt", FINANCE, 3)
    assert error.value.code == "SELECTION_CHANGED" and store.links == before


def test_empty_kb_cannot_be_approved_or_selected(store: FakeStore) -> None:
    store.kbs["kb-new"] = replace(store.kbs["kb-new"], file_count=0)
    with pytest.raises(RagRuleError) as review_error:
        service.approve_knowledge_base("kb-new", IT_USER, True)
    assert review_error.value.code == "EMPTY_KB"
    store.kbs["kb-new"] = replace(store.kbs["kb-new"], approval_status=APPROVAL_APPROVED)
    before = list(store.access)
    with pytest.raises(RagRuleError) as selection_error:
        service.set_qa_sources(["kb-new", "kb-tax"], "finance", FINANCE)
    assert selection_error.value.code == "EMPTY_KB" and store.access == before


def test_copy_only_links_into_pending_kb_with_creator_access(store: FakeStore) -> None:
    store.access.append(DepartmentAccess("kb-tax", "it", can_manage=True, use_for_qa=True))
    before_files, before_jobs = dict(store.files), dict(store.jobs)
    copied = service.copy_knowledge_base("kb-tax", "測試組合", IT)
    assert copied.approval_status == APPROVAL_PENDING and copied.file_count == 2
    assert {f for k, f in store.links if k == copied.knowledge_base_id} == {"f-txt", "f-pdf"}
    assert store.files == before_files and store.jobs == before_jobs
    assert [a for a in store.access if a.knowledge_base_id == copied.knowledge_base_id] == [
        DepartmentAccess(copied.knowledge_base_id, "it", can_manage=True)
    ]


@pytest.mark.parametrize(
    ("source_type", "status", "date", "code"),
    [
        ("unclassified", "unknown", None, "SOURCE_REQUIRED"),
        ("invalid", "unknown", None, "SOURCE_REQUIRED"),
        ("company_policy", "", None, "DATE_STATUS_REQUIRED"),
        ("company_policy", "known", None, "DATE_REQUIRED"),
        ("company_policy", "known", "2026-02-30", "DATE_REQUIRED"),
        ("company_policy", "unknown", "2026-10-31", "UNEXPECTED_DATE"),
        ("mixed", "not_applicable", "2026-10-31", "UNEXPECTED_DATE"),
    ],
)
def test_upload_metadata_rejected_before_storage(
    store: FakeStore,
    storage: FakeStorage,
    source_type: str,
    status: str,
    date: str | None,
    code: str,
) -> None:
    item = service.UploadItem(
        "x.txt",
        b"unique",
        source_type=source_type,
        effective_date_status=status,
        effective_date=date,
    )
    with pytest.raises(RagRuleError) as error:
        service._metadata(item)
    assert error.value.code == code
    (outcome,) = service.upload_documents(None, [item], FINANCE, storage)
    assert not outcome.ok and len(storage.objects) == 1


@pytest.mark.parametrize(
    "source_type",
    ["government_regulation", "company_policy", "operations_manual", "mixed", "other"],
)
def test_upload_stores_metadata_digest_and_verified_department_path(
    store: FakeStore, storage: FakeStorage, source_type: str
) -> None:
    store.departments[0] = DepartmentOption("finance", "finance_d", "財務部")
    raw = ("unique:" + source_type).encode()
    item = service.UploadItem(
        "x.txt",
        raw,
        source_type=source_type,
        effective_date_status="known",
        effective_date="2026-10-31",
        source_note=" 原始版本 ",
    )
    (outcome,) = service.upload_documents(None, [item], FINANCE, storage)
    assert outcome.ok
    created = next(f for f in store.files.values() if f.title == "x.txt")
    assert created.content_sha256 == hashlib.sha256(raw).hexdigest()
    assert created.effective_date is not None and created.effective_date.isoformat() == "2026-10-31"
    assert created.source_note == "原始版本" and created.source_type == source_type
    assert (
        created.storage_path.startswith("finance_d/RAG_")
        and created.owner_department_id == "finance"
    )
    assert not hasattr(created, "knowledge_base_id") and not any(
        f == created.rag_file_id for _, f in store.links
    )
    assert created.is_approved is False and created.parse_status == PARSE_UPLOADED


def test_duplicate_content_is_rejected_with_existing_title(
    store: FakeStore, storage: FakeStorage
) -> None:
    raw = b"duplicate original"
    store.files["f-txt"] = replace(
        store.files["f-txt"], content_sha256=hashlib.sha256(raw).hexdigest()
    )
    item = service.UploadItem(
        "copy.txt", raw, source_type="company_policy", effective_date_status="unknown"
    )
    (outcome,) = service.upload_documents(None, [item], FINANCE, storage)
    assert not outcome.ok and "作業要點" in outcome.message and "加入關聯" in outcome.message
    assert len(store.files) == 2 and len(storage.objects) == 1


def test_duplicate_legacy_file_with_null_hash_is_detected(
    store: FakeStore, storage: FakeStorage
) -> None:
    store.files["f-txt"] = replace(store.files["f-txt"], content_sha256=None)
    storage.objects[store.files["f-txt"].storage_path] = TXT.encode()
    item = service.UploadItem(
        "copy.txt", TXT.encode(), source_type="other", effective_date_status="not_applicable"
    )
    (outcome,) = service.upload_documents(None, [item], FINANCE, storage)
    assert not outcome.ok and "作業要點" in outcome.message
    assert store.files["f-txt"].content_sha256 == hashlib.sha256(TXT.encode()).hexdigest()


def test_unreadable_legacy_object_does_not_block_upload(
    store: FakeStore, storage: FakeStorage
) -> None:
    # f-txt 的物件不存在；只要有這種舊檔，先前的流程會擋下之後所有上傳
    store.files["f-txt"] = replace(store.files["f-txt"], content_sha256=None, storage_path=None)
    store.files["f-pdf"] = replace(store.files["f-pdf"], content_sha256=None)
    storage.objects[store.files["f-pdf"].storage_path] = b"legacy pdf"
    item = service.UploadItem(
        "new.txt", b"brand new", source_type="other", effective_date_status="unknown"
    )
    (outcome,) = service.upload_documents(None, [item], FINANCE, storage)
    assert outcome.ok
    assert store.files["f-txt"].content_sha256 is None
    assert store.files["f-pdf"].content_sha256 == hashlib.sha256(b"legacy pdf").hexdigest()


def test_legacy_hash_time_budget_stops_reading_but_upload_continues(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.files["f-txt"] = replace(store.files["f-txt"], content_sha256=None)
    storage.objects[store.files["f-txt"].storage_path] = TXT.encode()
    monkeypatch.setattr(service, "LEGACY_HASH_BUDGET_SECONDS", 0.0)
    item = service.UploadItem(
        "new.txt", b"brand new", source_type="other", effective_date_status="unknown"
    )
    (outcome,) = service.upload_documents(None, [item], FINANCE, storage)
    assert outcome.ok and store.files["f-txt"].content_sha256 is None


def test_legacy_read_waits_no_longer_than_remaining_budget(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.files["f-txt"] = replace(store.files["f-txt"], content_sha256=None)
    storage.objects[store.files["f-txt"].storage_path] = TXT.encode()
    monkeypatch.setattr(service, "LEGACY_HASH_BUDGET_SECONDS", 5.0)
    service._backfill_legacy_hashes(storage)
    assert len(storage.timeouts) == 1 and 0 < storage.timeouts[0] <= 5.0


def test_legacy_reads_stop_once_time_budget_is_used(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("f-txt", "f-pdf"):
        store.files[name] = replace(store.files[name], content_sha256=None)
        storage.objects[store.files[name].storage_path] = name.encode()
    clock = [0.0]
    monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    read = storage.get

    def slow_read(key: str, *, timeout_seconds: float = 30.0) -> tuple[bytes, str]:
        clock[0] += service.LEGACY_HASH_BUDGET_SECONDS
        return read(key, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(storage, "get", slow_read)
    service._backfill_legacy_hashes(storage)
    assert len(storage.timeouts) == 1


def _lose_commit_acknowledgement(store: FakeStore) -> None:
    """伺服器已提交，但 client 收不到確認而拋出例外。"""

    def lost() -> None:
        store._commit_access()
        raise rag_repository.SQLAlchemyError("connection lost")

    store.session.commit.side_effect = lost


def test_replacement_keeps_new_object_when_commit_outcome_is_unknown(
    store: FakeStore, storage: FakeStorage
) -> None:
    store.links = {(k, f) for k, f in store.links if f != "f-txt"}
    _lose_commit_acknowledgement(store)
    with pytest.raises(service.RagUnavailableError):
        service.replace_file_content("f-txt", "新版.txt", b"new", FINANCE, storage)
    current = store.files["f-txt"]
    assert current.file_version == 2 and storage.objects[current.storage_path] == b"new"
    assert storage.deleted == []


def test_upload_keeps_new_object_when_commit_outcome_is_unknown(
    store: FakeStore, storage: FakeStorage
) -> None:
    _lose_commit_acknowledgement(store)
    item = service.UploadItem(
        "new.txt", b"brand new", source_type="other", effective_date_status="unknown"
    )
    (outcome,) = service.upload_documents(None, [item], FINANCE, storage)
    created = next(f for f in store.files.values() if f.title == "new.txt")
    assert not outcome.ok and storage.objects[created.storage_path] == b"brand new"
    assert storage.deleted == []


def test_new_object_is_kept_when_file_state_cannot_be_confirmed(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object) -> None:
        raise rag_repository.SQLAlchemyError("synthetic failure")

    monkeypatch.setattr(rag_repository, "insert_file", broken)
    monkeypatch.setattr(rag_repository, "get_file", broken)
    item = service.UploadItem(
        "new.txt", b"brand new", source_type="other", effective_date_status="unknown"
    )
    (outcome,) = service.upload_documents(None, [item], FINANCE, storage)
    assert not outcome.ok and storage.deleted == []


def test_legacy_hash_saved_file_by_file_keeps_earlier_progress(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.files["f-txt"] = replace(store.files["f-txt"], content_sha256=None)
    store.files["f-pdf"] = replace(store.files["f-pdf"], content_sha256=None)
    storage.objects[store.files["f-txt"].storage_path] = TXT.encode()
    storage.objects[store.files["f-pdf"].storage_path] = b"legacy pdf"
    original = rag_repository.set_legacy_hash

    def fail_on_second(db: object, file_id: str, version: int, digest: str) -> None:
        if file_id == "f-pdf":
            raise rag_repository.SQLAlchemyError("synthetic failure")
        original(db, file_id, version, digest)

    monkeypatch.setattr(rag_repository, "set_legacy_hash", fail_on_second)
    service._backfill_legacy_hashes(storage)
    assert store.files["f-txt"].content_sha256 == hashlib.sha256(TXT.encode()).hexdigest()
    assert store.files["f-pdf"].content_sha256 is None


def test_legacy_hash_database_failure_does_not_block_upload(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.files["f-txt"] = replace(store.files["f-txt"], content_sha256=None)
    storage.objects[store.files["f-txt"].storage_path] = TXT.encode()

    def broken(db: object, file_id: str, version: int, digest: str) -> None:
        raise rag_repository.SQLAlchemyError("synthetic failure")

    monkeypatch.setattr(rag_repository, "set_legacy_hash", broken)
    item = service.UploadItem(
        "new.txt", b"brand new", source_type="other", effective_date_status="unknown"
    )
    (outcome,) = service.upload_documents(None, [item], FINANCE, storage)
    assert outcome.ok and any(f.title == "new.txt" for f in store.files.values())


def test_legacy_hash_listing_failure_does_not_block_replacement(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.links = {(k, f) for k, f in store.links if f != "f-txt"}

    def broken(db: object) -> list[RagFile]:
        raise rag_repository.SQLAlchemyError("synthetic failure")

    monkeypatch.setattr(rag_repository, "list_unhashed_files", broken)
    updated, _job = service.replace_file_content("f-txt", "新版.txt", b"new", FINANCE, storage)
    assert updated.file_version == 2


def test_replacement_in_same_second_never_overwrites_current_object(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.links = {(k, f) for k, f in store.links if f != "f-txt"}
    moment = datetime(2026, 10, 31, 8, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(service, "_now", lambda: moment)
    first, _job = service.replace_file_content("f-txt", "新版.txt", b"second", FINANCE, storage)

    def broken(db: object, rag_file: RagFile) -> int:
        raise rag_repository.SQLAlchemyError("synthetic failure")

    monkeypatch.setattr(rag_repository, "replace_file_content", broken)
    with pytest.raises(service.RagUnavailableError):
        service.replace_file_content("f-txt", "新版.txt", b"third", FINANCE, storage)
    assert storage.objects[first.storage_path] == b"second"
    assert first.storage_path not in storage.deleted
    assert sum(content == b"third" for content in storage.objects.values()) == 0


def test_replacement_path_keeps_file_id_and_adds_revision(
    store: FakeStore, storage: FakeStorage
) -> None:
    store.links = {(k, f) for k, f in store.links if f != "f-txt"}
    updated, _job = service.replace_file_content("f-txt", "新版.txt", b"new", FINANCE, storage)
    assert re.fullmatch(r"finance/RAG_\d{14}_f-txt_[0-9a-f]{8}\.txt", updated.storage_path or "")


def test_replace_by_it_keeps_uploader_department_path(
    store: FakeStore, storage: FakeStorage
) -> None:
    store.links = {(k, f) for k, f in store.links if f != "f-txt"}
    store.departments = [
        DepartmentOption("finance", "finance_d", "財務部"),
        DepartmentOption("it", "technology_d", "資訊部"),
    ]
    updated, _job = service.replace_file_content("f-txt", "新版.txt", b"new", IT, storage)
    assert (updated.storage_path or "").startswith("finance_d/RAG_")
    assert updated.owner_department_id == "finance"


def test_replace_keeps_path_of_inactive_uploader_department(
    store: FakeStore, storage: FakeStorage
) -> None:
    store.links = {(k, f) for k, f in store.links if f != "f-txt"}
    store.departments = [DepartmentOption("it", "technology_d", "資訊部")]
    store.inactive_departments = [DepartmentOption("finance", "finance_d", "財務部")]
    updated, _job = service.replace_file_content("f-txt", "新版.txt", b"new", IT, storage)
    assert (updated.storage_path or "").startswith("finance_d/RAG_")


def test_replace_rejects_file_without_uploader_department(
    store: FakeStore, storage: FakeStorage
) -> None:
    store.links = {(k, f) for k, f in store.links if f != "f-txt"}
    store.files["f-txt"] = replace(store.files["f-txt"], owner_department_id=None)
    objects = dict(storage.objects)
    with pytest.raises(RagRuleError) as error:
        service.replace_file_content("f-txt", "新版.txt", b"new", IT, storage)
    assert error.value.code == "NO_OWNER_DEPARTMENT" and storage.objects == objects
    assert store.files["f-txt"].file_version == 1


def test_replace_locks_only_the_file_to_avoid_deadlock_with_add_files(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    # add_files 先鎖知識庫再鎖檔案；替換仍被待審查知識庫引用時，也不可反向鎖定
    store.links = {("kb-new", "f-txt")}

    def unexpected(db: object, kb_id: str) -> None:
        pytest.fail("替換內容不應鎖定知識庫")

    monkeypatch.setattr(rag_repository, "lock_knowledge_base", unexpected)
    service.replace_file_content("f-txt", "新版.txt", b"new", FINANCE, storage)
    assert store.files["f-txt"].file_version == 2


def test_job_start_locks_file_before_job(store: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    # 寫回結果與刪除檔案都是先檔案後工作；開始工作若相反，兩者同時執行會互相等待
    order: list[str] = []
    lock_file, lock_job = rag_repository.lock_file, rag_repository.lock_job

    def record_file(db: object, file_id: str) -> RagFile | None:
        order.append("file")
        return lock_file(db, file_id)

    def record_job(db: object, job_id: str) -> RagJob | None:
        order.append("job")
        return lock_job(db, job_id)

    monkeypatch.setattr(rag_repository, "lock_file", record_file)
    monkeypatch.setattr(rag_repository, "lock_job", record_job)
    service.process_job("job-txt", FakeEmbedding(), read_object=_reader(TXT.encode()))
    assert order == ["file", "job"]


def test_job_locked_by_other_worker_is_rejected_after_file_lock(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rag_repository, "lock_job", lambda db, job_id: None)
    with pytest.raises(RagRuleError) as error:
        service.process_job("job-txt", FakeEmbedding(), read_object=_reader(TXT.encode()))
    assert error.value.code == "NOT_FOUND"
    assert store.files["f-txt"].parse_status == PARSE_UPLOADED


def test_file_library_loads_references_without_per_file_queries(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def per_file(db: object, file_id: str) -> tuple[KnowledgeBase, ...]:
        pytest.fail("檔案清單不應逐檔查詢引用")

    monkeypatch.setattr(rag_repository, "list_file_references", per_file)
    library = service.load_file_library()
    assert {f.rag_file_id for f in library.files} == {"f-txt", "f-pdf"}
    assert {k.knowledge_base_id for k in library.references["f-pdf"]} == {"kb-tax", "kb-new"}


def test_upload_in_kb_inserts_link_without_reindexing_existing_files(
    store: FakeStore, storage: FakeStorage
) -> None:
    item = service.UploadItem(
        "new.txt", b"brand new", source_type="other", effective_date_status="unknown"
    )
    before_jobs = dict(store.jobs)
    (outcome,) = service.upload_documents("kb-new", [item], FINANCE, storage)
    assert outcome.ok
    created = next(f for f in store.files.values() if f.title == "new.txt")
    assert ("kb-new", created.rag_file_id) in store.links and store.kbs["kb-new"].file_count == 2
    assert all(store.jobs[k] == v for k, v in before_jobs.items())


def test_link_failure_rolls_back_file_and_job_and_removes_new_object(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    before_files, before_links = dict(store.files), set(store.links)
    monkeypatch.setattr(
        rag_repository,
        "insert_file_link",
        lambda *args: (_ for _ in ()).throw(rag_repository.SQLAlchemyError("synthetic failure")),
    )
    item = service.UploadItem(
        "new.txt", b"brand new", source_type="company_policy", effective_date_status="unknown"
    )
    (outcome,) = service.upload_documents("kb-new", [item], FINANCE, storage)
    assert not outcome.ok and store.files == before_files and store.links == before_links
    assert len(storage.deleted) == 1 and storage.deleted[0] not in storage.objects


def test_upload_rechecks_duplicate_after_object_put(
    store: FakeStore, storage: FakeStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = b"concurrent duplicate"
    original_put = storage.put

    def concurrent_put(key: str, content: bytes, *, content_type: str) -> None:
        original_put(key, content, content_type=content_type)
        store.files["concurrent"] = RagFile(
            "concurrent", "另一個上傳", content_sha256=hashlib.sha256(content).hexdigest()
        )

    monkeypatch.setattr(storage, "put", concurrent_put)
    item = service.UploadItem("new.txt", raw, source_type="other", effective_date_status="unknown")
    (outcome,) = service.upload_documents(None, [item], FINANCE, storage)
    assert not outcome.ok and "另一個上傳" in outcome.message
    assert len(storage.deleted) == 1 and storage.deleted[0] not in storage.objects
    assert not any(f.title == "new.txt" for f in store.files.values())


def test_delete_without_storage_configuration_rolls_back_db(
    store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.links = {(k, f) for k, f in store.links if f != "f-txt"}

    def unavailable() -> service.RagStorage:
        raise service.RagUnavailableError("synthetic unavailable")

    monkeypatch.setattr(service, "rag_storage", unavailable)
    with pytest.raises(service.RagUnavailableError):
        service.delete_file("f-txt", FINANCE)
    assert "f-txt" in store.files
    store.session.rollback.assert_called_once()
    store.session.commit.assert_not_called()


def test_unknown_relation_cannot_be_removed(store: FakeStore) -> None:
    before = set(store.links)
    with pytest.raises(RagRuleError) as error:
        service.remove_file("kb-new", "f-txt", FINANCE, 1)
    assert error.value.code == "NOT_FOUND" and store.links == before
