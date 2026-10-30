"""
模組名稱：app.services.rag_review_service
功能說明：RAG 資訊部審核——知識庫核准／退回／重新送審、文件核准與內容替換後撤銷核准、待審清單。
相依模組：app.schemas.rag
注意事項：
    - 本模組不存取資料庫；回傳新的不可變物件，由呼叫端交給 repository 在同一交易寫入。
    - v1.5 沒有角色欄位；是否為資訊部由呼叫端判斷後以 is_it_reviewer 傳入
      （畫面暫以目前部門名稱含「資訊」或代碼 IT 判斷，正式權限待 E／G）。
    - 退回知識庫時，同一交易清除所有部門的 use_for_qa（v1.5 差異第 3 點）。
"""

from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from app.schemas.rag import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    JOB_PENDING,
    JOB_REINDEX,
    PARSE_READY,
    PARSE_UPLOADED,
    DepartmentAccess,
    KnowledgeBase,
    PendingReviews,
    RagFile,
    RagJob,
    RagRuleError,
)


def _require_it(is_it_reviewer: bool) -> None:
    if not is_it_reviewer:
        raise RagRuleError("NOT_IT_REVIEWER", "只有資訊部可以審核知識庫與文件")


def approve_knowledge_base(
    kb: KnowledgeBase, reviewer: str, is_it_reviewer: bool, now: datetime
) -> KnowledgeBase:
    """資訊部核准待審核的知識庫。

    Args:
        kb: 知識庫。
        reviewer: 審核者 user_id。
        is_it_reviewer: 呼叫端判斷審核者是否屬資訊部。
        now: 審核時間。

    Returns:
        approved 的知識庫，寫入 reviewed_by／reviewed_at，清除退回原因。

    Raises:
        RagRuleError: 不是資訊部，或知識庫不在待審核。
    """
    _require_it(is_it_reviewer)
    if kb.approval_status != APPROVAL_PENDING:
        raise RagRuleError("NOT_PENDING", "只有待審核的知識庫可以核准")
    return replace(
        kb,
        approval_status=APPROVAL_APPROVED,
        reviewed_by=reviewer,
        reviewed_at=now,
        review_comment=None,
    )


def reject_knowledge_base(
    kb: KnowledgeBase,
    access_rows: tuple[DepartmentAccess, ...],
    reviewer: str,
    is_it_reviewer: bool,
    comment: str,
    now: datetime,
) -> tuple[KnowledgeBase, tuple[DepartmentAccess, ...]]:
    """資訊部退回知識庫（原因必填），並清除所有部門對它的問答選用。

    待審核與已核准的知識庫都可退回（已核准者退回即撤銷核准）。

    Args:
        kb: 知識庫。
        access_rows: 該知識庫的 rag_department_access。
        reviewer: 審核者 user_id。
        is_it_reviewer: 呼叫端判斷審核者是否屬資訊部。
        comment: 退回原因，必填。
        now: 審核時間。

    Returns:
        (rejected 的知識庫, use_for_qa 全部為 False 的部門列)，須同一交易寫入。

    Raises:
        RagRuleError: 不是資訊部、已是退回狀態，或原因空白。
    """
    _require_it(is_it_reviewer)
    if kb.approval_status == APPROVAL_REJECTED:
        raise RagRuleError("ALREADY_REJECTED", "此知識庫已退回，等待重新送審")
    reason = comment.strip()
    if not reason:
        raise RagRuleError("COMMENT_REQUIRED", "退回時須填寫原因")
    rejected = replace(
        kb,
        approval_status=APPROVAL_REJECTED,
        reviewed_by=reviewer,
        reviewed_at=now,
        review_comment=reason,
    )
    cleared = tuple(
        replace(row, use_for_qa=False)
        for row in access_rows
        if row.knowledge_base_id == kb.knowledge_base_id
    )
    return rejected, cleared


def resubmit_knowledge_base(
    kb: KnowledgeBase, submitted_by: str, submitted_department_id: str | None
) -> KnowledgeBase:
    """退回的知識庫重新送審：回到 pending，更新 submitted_*、清空 reviewed_*。

    退回原因保留，讓送審者與審核者仍看得到上次退回的理由。

    Args:
        kb: 知識庫。
        submitted_by: 送審者 user_id。
        submitted_department_id: 送審者目前部門。

    Returns:
        pending 的知識庫。

    Raises:
        RagRuleError: 知識庫不在退回狀態。
    """
    if kb.approval_status != APPROVAL_REJECTED:
        raise RagRuleError("NOT_REJECTED", "只有已退回的知識庫可以重新送審")
    return replace(
        kb,
        approval_status=APPROVAL_PENDING,
        submitted_by=submitted_by,
        submitted_department_id=submitted_department_id,
        reviewed_by=None,
        reviewed_at=None,
    )


def approve_file(rag_file: RagFile, reviewer: str, is_it_reviewer: bool, now: datetime) -> RagFile:
    """資訊部核准文件；文件須已完成索引（parse_status = ready）。

    Args:
        rag_file: 文件。
        reviewer: 審核者 user_id。
        is_it_reviewer: 呼叫端判斷審核者是否屬資訊部。
        now: 核准時間。

    Returns:
        is_approved 為 True 的文件。

    Raises:
        RagRuleError: 不是資訊部、尚未索引完成，或已核准。
    """
    _require_it(is_it_reviewer)
    if rag_file.parse_status != PARSE_READY:
        raise RagRuleError("NOT_READY", "文件尚未完成索引，不能核准")
    if rag_file.is_approved:
        raise RagRuleError("ALREADY_APPROVED", "此文件已核准")
    return replace(rag_file, is_approved=True, approved_by=reviewer, approved_at=now)


def suspend_file(rag_file: RagFile, is_it_reviewer: bool) -> RagFile:
    """資訊部暫停文件：取消核准、清空 approved_*；版本與索引不變（v1.5 文件只有核准／暫停）。

    Args:
        rag_file: 文件。
        is_it_reviewer: 呼叫端判斷操作者是否屬資訊部。

    Returns:
        is_approved 為 False 的文件。

    Raises:
        RagRuleError: 不是資訊部，或文件本來就未核准。
    """
    _require_it(is_it_reviewer)
    if not rag_file.is_approved:
        raise RagRuleError("NOT_APPROVED", "此文件目前未核准")
    return replace(rag_file, is_approved=False, approved_by=None, approved_at=None)


def revoke_file_approval(rag_file: RagFile) -> tuple[RagFile, RagJob]:
    """文件內容替換或改文字：版本 +1、撤銷核准、重新排入索引。

    依 v1.5 差異第 4 點，下列須同一交易寫入：file_version +1、is_approved = false、
    清空 approved_*、parse_status = uploaded、新增 rag_jobs（reindex）。新版本索引完成前
    不得沿用舊的 ready。

    Args:
        rag_file: 內容替換前的文件。

    Returns:
        (更新後的文件, 新的 reindex 工作)。
    """
    updated = replace(
        rag_file,
        file_version=rag_file.file_version + 1,
        is_approved=False,
        approved_by=None,
        approved_at=None,
        parse_status=PARSE_UPLOADED,
    )
    job = RagJob(
        job_id=str(uuid4()),
        rag_file_id=rag_file.rag_file_id,
        job_type=JOB_REINDEX,
        job_status=JOB_PENDING,
    )
    return updated, job


def list_pending_reviews(
    knowledge_bases: list[KnowledgeBase], files: list[RagFile]
) -> PendingReviews:
    """資訊部待審清單：待審核的知識庫，以及尚未核准的文件（已索引者在前）。

    Args:
        knowledge_bases: 知識庫。
        files: 文件。

    Returns:
        待審清單。
    """
    pending_kbs = tuple(kb for kb in knowledge_bases if kb.approval_status == APPROVAL_PENDING)
    unapproved = [f for f in files if not f.is_approved]
    unapproved.sort(key=lambda f: f.parse_status != PARSE_READY)
    return PendingReviews(knowledge_bases=pending_kbs, files=tuple(unapproved))
