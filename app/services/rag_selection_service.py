"""
模組名稱：app.services.rag_selection_service
功能說明：RAG 部門選用——指派部門、整組設定多個問答知識庫。
相依模組：app.schemas.rag
注意事項：
    - 本模組不存取資料庫；回傳新的部門列，由呼叫端交給 repository 在同一交易寫入。
    - 一個部門可同時選用多個知識庫；全部驗證後才回傳整組取代的結果。
    - 只能選 approved 且 is_active 的知識庫，且該部門需 can_read。
"""

from collections.abc import Sequence
from dataclasses import replace

from app.schemas.rag import (
    APPROVAL_APPROVED,
    DepartmentAccess,
    KnowledgeBase,
    RagActor,
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


def set_qa_sources(
    kbs: Sequence[KnowledgeBase], access_rows: tuple[DepartmentAccess, ...], department_id: str
) -> tuple[DepartmentAccess, ...]:
    """整組取代部門問答選用；空清單全部取消，其他部門保持原選用。

    Args:
        kbs: 要選用的全部知識庫；重複項目只算一次。
        access_rows: 目前的 rag_department_access（含其他知識庫的列）。
        department_id: 部門。

    Returns:
        全部驗證成功後的新列；該部門 use_for_qa 恰好等於所選清單。

    Raises:
        RagRuleError: 知識庫未核准或已停用，或部門未指派／不可讀。
    """
    selected = {kb.knowledge_base_id: kb for kb in kbs}
    readable = {
        r.knowledge_base_id for r in access_rows if r.department_id == department_id and r.can_read
    }
    for kb in selected.values():
        if kb.approval_status != APPROVAL_APPROVED or not kb.is_active:
            raise RagRuleError("KB_NOT_APPROVED", "只能指派已核准且啟用中的知識庫")
        if kb.file_count < 1:
            raise RagRuleError("EMPTY_KB", "空知識庫不可部門指派")
        if kb.knowledge_base_id not in readable:
            raise RagRuleError("NO_READ_ACCESS", "此部門未開放或不可讀此知識庫")
    return tuple(
        replace(r, use_for_qa=(r.knowledge_base_id in selected))
        if r.department_id == department_id
        else r
        for r in access_rows
    )
