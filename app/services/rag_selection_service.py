"""
模組名稱：app.services.rag_selection_service
功能說明：RAG 部門選用——指派部門、選用問答知識庫（一個部門一個）、文件於部門的選用、部門選用總覽。
相依模組：app.schemas.rag
注意事項：
    - 本模組不存取資料庫；回傳新的部門列，由呼叫端交給 repository 在同一交易寫入。
    - 一個部門同時只能選用一個知識庫（ux_rag_department_access_qa）：切換時同一交易先關舊、再開新。
    - 只能選 approved 且 is_active 的知識庫，且該部門需 can_read。
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.schemas.rag import (
    APPROVAL_APPROVED,
    DepartmentAccess,
    FileDepartmentUsage,
    KnowledgeBase,
    RagActor,
    RagFile,
    RagRuleError,
)


def can_manage_knowledge_base(
    kb: KnowledgeBase, access_rows: Sequence[DepartmentAccess], actor: RagActor
) -> bool:
    """目前部門可否管理此知識庫（改名、刪除、上傳替換、指派部門、索引）。

    資訊部可管理全部；其他部門限送審部門，或部門配置 can_manage 為真。
    功能操作權限（新增／修改／刪除）另由路由以 module.check 驗證。

    Args:
        kb: 知識庫。
        access_rows: 此知識庫的部門配置。
        actor: 操作者。

    Returns:
        可管理為 True。
    """
    if actor.is_it or kb.submitted_department_id == actor.department_id:
        return True
    return any(
        row.knowledge_base_id == kb.knowledge_base_id
        and row.department_id == actor.department_id
        and row.can_manage
        for row in access_rows
    )


@dataclass(frozen=True)
class DepartmentUsage:
    """一個部門的選用總覽。

    Attributes:
        qa_source: 目前選用問答的知識庫；未選用為 None。
        readable: 已指派且可讀的知識庫。
        documents: (文件, 是否於此部門選用)；只列 qa_source 的文件。
    """

    department_id: str
    qa_source: KnowledgeBase | None
    readable: tuple[KnowledgeBase, ...]
    documents: tuple[tuple[RagFile, bool], ...]


def assign_department(
    access_rows: tuple[DepartmentAccess, ...],
    knowledge_base_id: str,
    department_id: str,
    *,
    can_read: bool = True,
    can_manage: bool = False,
) -> tuple[DepartmentAccess, ...]:
    """指派（或更新）部門對知識庫的權限；取消可讀時同時取消問答選用。

    Args:
        access_rows: 目前的 rag_department_access。
        knowledge_base_id: 知識庫。
        department_id: 部門。
        can_read: 可讀。
        can_manage: 可編輯。

    Returns:
        更新後的全部部門列。
    """
    rows = list(access_rows)
    for index, row in enumerate(rows):
        if (row.knowledge_base_id, row.department_id) == (knowledge_base_id, department_id):
            rows[index] = replace(
                row,
                can_read=can_read,
                can_manage=can_manage,
                use_for_qa=row.use_for_qa and can_read,
            )
            return tuple(rows)
    rows.append(DepartmentAccess(knowledge_base_id, department_id, can_read, can_manage))
    return tuple(rows)


def set_qa_source(
    kb: KnowledgeBase, access_rows: tuple[DepartmentAccess, ...], department_id: str
) -> tuple[DepartmentAccess, ...]:
    """部門選用此知識庫問答；同部門原本選用的其他知識庫一併取消。

    Args:
        kb: 要選用的知識庫。
        access_rows: 目前的 rag_department_access（含其他知識庫的列）。
        department_id: 部門。

    Returns:
        更新後的全部部門列；該部門只有一列 use_for_qa 為 True。

    Raises:
        RagRuleError: 知識庫未核准或已停用，或部門未指派／不可讀。
    """
    if kb.approval_status != APPROVAL_APPROVED or not kb.is_active:
        raise RagRuleError("KB_NOT_APPROVED", "只能選用已核准且啟用中的知識庫")
    target = (kb.knowledge_base_id, department_id)
    row = next((r for r in access_rows if (r.knowledge_base_id, r.department_id) == target), None)
    if row is None or not row.can_read:
        raise RagRuleError("NO_READ_ACCESS", "此部門未指派或不可讀此知識庫")
    return tuple(
        replace(r, use_for_qa=(r.knowledge_base_id == kb.knowledge_base_id))
        if r.department_id == department_id
        else r
        for r in access_rows
    )


def clear_qa_source(
    access_rows: tuple[DepartmentAccess, ...], department_id: str
) -> tuple[DepartmentAccess, ...]:
    """取消部門的問答選用。

    Args:
        access_rows: 目前的 rag_department_access。
        department_id: 部門。

    Returns:
        更新後的全部部門列。
    """
    return tuple(
        replace(r, use_for_qa=False) if r.department_id == department_id else r for r in access_rows
    )


def set_document_usage(
    usage_rows: tuple[FileDepartmentUsage, ...],
    access_rows: tuple[DepartmentAccess, ...],
    rag_file: RagFile,
    department_id: str,
    use_document: bool,
    updated_by: str | None = None,
) -> tuple[FileDepartmentUsage, ...]:
    """設定文件在某部門是否選用。

    Args:
        usage_rows: 目前的 rag_file_department_usage。
        access_rows: 目前的 rag_department_access。
        rag_file: 文件。
        department_id: 部門。
        use_document: 是否選用。
        updated_by: 修改者 user_id。

    Returns:
        更新後的全部文件選用列。

    Raises:
        RagRuleError: 部門不可讀此文件所屬知識庫。
    """
    readable = any(
        r.knowledge_base_id == rag_file.knowledge_base_id
        and r.department_id == department_id
        and r.can_read
        for r in access_rows
    )
    if not readable:
        raise RagRuleError("NO_READ_ACCESS", "此部門不可讀這份文件所屬的知識庫")
    rows = [
        r
        for r in usage_rows
        if (r.rag_file_id, r.department_id) != (rag_file.rag_file_id, department_id)
    ]
    rows.append(FileDepartmentUsage(rag_file.rag_file_id, department_id, use_document, updated_by))
    return tuple(rows)


def list_department_usage(
    department_id: str,
    knowledge_bases: list[KnowledgeBase],
    access_rows: tuple[DepartmentAccess, ...],
    files: list[RagFile],
    usage_rows: tuple[FileDepartmentUsage, ...],
) -> DepartmentUsage:
    """整理一個部門的選用總覽。

    Args:
        department_id: 部門。
        knowledge_bases: 知識庫。
        access_rows: rag_department_access。
        files: 文件。
        usage_rows: rag_file_department_usage；沒有列視為不選用。

    Returns:
        部門選用總覽。
    """
    by_id = {kb.knowledge_base_id: kb for kb in knowledge_bases}
    mine = [r for r in access_rows if r.department_id == department_id]
    readable = tuple(
        by_id[r.knowledge_base_id] for r in mine if r.can_read and r.knowledge_base_id in by_id
    )
    qa_row = next((r for r in mine if r.use_for_qa), None)
    qa_source = by_id.get(qa_row.knowledge_base_id) if qa_row else None
    used = {
        r.rag_file_id for r in usage_rows if r.department_id == department_id and r.use_document
    }
    documents = tuple(
        (f, f.rag_file_id in used)
        for f in files
        if qa_source is not None and f.knowledge_base_id == qa_source.knowledge_base_id
    )
    return DepartmentUsage(department_id, qa_source, readable, documents)
