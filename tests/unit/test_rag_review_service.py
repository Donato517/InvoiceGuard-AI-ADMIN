"""
模組名稱：tests.unit.test_rag_review_service
功能說明：RAG 資訊部審核規則測試——知識庫核准／退回（清除問答選用）／重新送審、文件核准與內容替換。
注意事項：純邏輯測試，不連資料庫。
"""

from datetime import UTC, datetime

import pytest

from app.schemas.rag import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    JOB_PENDING,
    JOB_REINDEX,
    PARSE_FAILED,
    PARSE_READY,
    PARSE_UPLOADED,
    DepartmentAccess,
    KnowledgeBase,
    RagFile,
    RagRuleError,
)
from app.services.rag_review_service import (
    approve_file,
    approve_knowledge_base,
    list_pending_reviews,
    reject_knowledge_base,
    resubmit_knowledge_base,
    revoke_file_approval,
)

NOW = datetime(2026, 10, 30, 22, 0, tzinfo=UTC)
KB = KnowledgeBase("kb-1", "營業稅申報")


def test_approve_pending_knowledge_base() -> None:
    approved = approve_knowledge_base(KB, "it-user", True, NOW)
    assert approved.approval_status == APPROVAL_APPROVED
    assert (approved.reviewed_by, approved.reviewed_at) == ("it-user", NOW)


def test_only_it_department_can_review() -> None:
    with pytest.raises(RagRuleError) as error:
        approve_knowledge_base(KB, "fin-user", False, NOW)
    assert error.value.code == "NOT_IT_REVIEWER"


def test_approve_requires_pending() -> None:
    approved = approve_knowledge_base(KB, "it-user", True, NOW)
    with pytest.raises(RagRuleError) as error:
        approve_knowledge_base(approved, "it-user", True, NOW)
    assert error.value.code == "NOT_PENDING"


def test_reject_requires_comment() -> None:
    with pytest.raises(RagRuleError) as error:
        reject_knowledge_base(KB, (), "it-user", True, "  ", NOW)
    assert error.value.code == "COMMENT_REQUIRED"


def test_reject_clears_every_department_qa_selection() -> None:
    approved = approve_knowledge_base(KB, "it-user", True, NOW)
    rows = (
        DepartmentAccess("kb-1", "finance", use_for_qa=True),
        DepartmentAccess("kb-1", "procurement", use_for_qa=True),
        DepartmentAccess("kb-2", "finance", use_for_qa=False),
    )
    rejected, cleared = reject_knowledge_base(approved, rows, "it-user", True, "來源過舊", NOW)
    assert rejected.approval_status == APPROVAL_REJECTED
    assert rejected.review_comment == "來源過舊"
    assert {r.department_id for r in cleared} == {"finance", "procurement"}
    assert not any(r.use_for_qa for r in cleared)


def test_resubmit_returns_to_pending_and_clears_review() -> None:
    rejected, _ = reject_knowledge_base(KB, (), "it-user", True, "缺說明", NOW)
    again = resubmit_knowledge_base(rejected, "fin-user", "finance")
    assert again.approval_status == APPROVAL_PENDING
    assert (again.submitted_by, again.submitted_department_id) == ("fin-user", "finance")
    assert (again.reviewed_by, again.reviewed_at) == (None, None)
    assert again.review_comment == "缺說明"


def test_resubmit_requires_rejected() -> None:
    with pytest.raises(RagRuleError) as error:
        resubmit_knowledge_base(KB, "fin-user", "finance")
    assert error.value.code == "NOT_REJECTED"


def test_approve_file_requires_ready() -> None:
    uploaded = RagFile("f-1", "kb-1", "營業稅法", parse_status=PARSE_UPLOADED)
    with pytest.raises(RagRuleError) as error:
        approve_file(uploaded, "it-user", True, NOW)
    assert error.value.code == "NOT_READY"
    failed = RagFile("f-1", "kb-1", "掃描檔", parse_status=PARSE_FAILED)
    with pytest.raises(RagRuleError):
        approve_file(failed, "it-user", True, NOW)


def test_approve_ready_file() -> None:
    ready = RagFile("f-1", "kb-1", "營業稅法", parse_status=PARSE_READY)
    approved = approve_file(ready, "it-user", True, NOW)
    assert (approved.is_approved, approved.approved_by, approved.approved_at) == (
        True,
        "it-user",
        NOW,
    )
    with pytest.raises(RagRuleError):
        approve_file(ready, "fin-user", False, NOW)


def test_content_replacement_bumps_version_and_queues_reindex() -> None:
    approved = RagFile(
        "f-1",
        "kb-1",
        "營業稅法",
        file_version=2,
        parse_status=PARSE_READY,
        is_approved=True,
        approved_by="it-user",
        approved_at=NOW,
    )
    updated, job = revoke_file_approval(approved)
    assert updated.file_version == 3
    assert (updated.is_approved, updated.approved_by, updated.approved_at) == (False, None, None)
    assert updated.parse_status == PARSE_UPLOADED
    assert (job.rag_file_id, job.job_type, job.job_status) == ("f-1", JOB_REINDEX, JOB_PENDING)


def test_pending_reviews_lists_ready_files_first() -> None:
    kbs = [KB, approve_knowledge_base(KnowledgeBase("kb-2", "其他"), "it", True, NOW)]
    files = [
        RagFile("f-1", "kb-1", "等待索引"),
        RagFile("f-2", "kb-1", "已索引", parse_status=PARSE_READY),
        RagFile(
            "f-3", "kb-1", "已核准", parse_status=PARSE_READY, is_approved=True, approved_at=NOW
        ),
    ]
    pending = list_pending_reviews(kbs, files)
    assert [kb.knowledge_base_id for kb in pending.knowledge_bases] == ["kb-1"]
    assert [f.rag_file_id for f in pending.files] == ["f-2", "f-1"]
