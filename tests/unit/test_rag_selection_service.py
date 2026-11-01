"""
模組名稱：tests.unit.test_rag_selection_service
功能說明：RAG 部門選用規則測試——指派、多個問答知識庫整組取代、需已核准與可讀。
注意事項：純邏輯測試，不連資料庫。
"""

import pytest

from app.schemas.rag import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    DepartmentAccess,
    KnowledgeBase,
    RagRuleError,
)
from app.services.rag_selection_service import (
    assign_department,
    set_qa_sources,
)

TAX = KnowledgeBase("kb-tax", "營業稅申報", approval_status=APPROVAL_APPROVED, file_count=1)
LAWS = KnowledgeBase("kb-laws", "發票與會計法規", approval_status=APPROVAL_APPROVED, file_count=1)
ROWS = (
    DepartmentAccess("kb-tax", "finance", use_for_qa=True),
    DepartmentAccess("kb-laws", "finance"),
    DepartmentAccess("kb-tax", "procurement", use_for_qa=True),
)


def _qa(rows: tuple[DepartmentAccess, ...], department_id: str) -> list[str]:
    return [r.knowledge_base_id for r in rows if r.department_id == department_id and r.use_for_qa]


def test_saving_qa_sources_replaces_selection_for_target_department() -> None:
    rows = set_qa_sources([LAWS], ROWS, "finance")
    assert _qa(rows, "finance") == ["kb-laws"]
    assert _qa(rows, "procurement") == ["kb-tax"]


def test_qa_source_requires_approved_and_active() -> None:
    pending = KnowledgeBase(
        "kb-laws", "發票與會計法規", approval_status=APPROVAL_PENDING, file_count=1
    )
    with pytest.raises(RagRuleError) as error:
        set_qa_sources([TAX, pending], ROWS, "finance")
    assert error.value.code == "KB_NOT_APPROVED"
    inactive = KnowledgeBase(
        "kb-laws", "x", approval_status=APPROVAL_APPROVED, is_active=False, file_count=1
    )
    with pytest.raises(RagRuleError):
        set_qa_sources([TAX, inactive], ROWS, "finance")


def test_qa_source_requires_read_access() -> None:
    with pytest.raises(RagRuleError) as error:
        set_qa_sources([LAWS], ROWS, "procurement")
    assert error.value.code == "NO_READ_ACCESS"
    unreadable = (DepartmentAccess("kb-laws", "finance", can_read=False),)
    with pytest.raises(RagRuleError):
        set_qa_sources([LAWS], unreadable, "finance")


def test_unassigning_read_also_clears_qa() -> None:
    rows = assign_department(ROWS, "kb-tax", "finance", can_read=False)
    row = next(r for r in rows if (r.knowledge_base_id, r.department_id) == ("kb-tax", "finance"))
    assert (row.can_read, row.use_for_qa) == (False, False)


def test_assign_new_department_appends_row() -> None:
    rows = assign_department(ROWS, "kb-laws", "it", can_manage=True)
    assert DepartmentAccess("kb-laws", "it", can_read=True, can_manage=True) in rows


def test_empty_selection_clears_all() -> None:
    assert _qa(set_qa_sources([], ROWS, "finance"), "finance") == []


def test_multiple_qa_sources_and_duplicates() -> None:
    rows = set_qa_sources([TAX, LAWS, TAX], ROWS, "finance")
    assert _qa(rows, "finance") == ["kb-tax", "kb-laws"]
    assert _qa(rows, "procurement") == ["kb-tax"]
    assert _qa(ROWS, "finance") == ["kb-tax"]  # 輸入維持不可變
    replaced = set_qa_sources([LAWS], rows, "finance")
    assert _qa(replaced, "finance") == ["kb-laws"]
    assert _qa(set_qa_sources([], rows, "finance"), "finance") == []
