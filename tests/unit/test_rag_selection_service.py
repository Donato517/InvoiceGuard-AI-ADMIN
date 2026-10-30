"""
模組名稱：tests.unit.test_rag_selection_service
功能說明：RAG 部門選用規則測試——指派、一個部門一個問答知識庫、需已核准與可讀、文件選用與總覽。
注意事項：純邏輯測試，不連資料庫。
"""

import pytest

from app.schemas.rag import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    DepartmentAccess,
    FileDepartmentUsage,
    KnowledgeBase,
    RagFile,
    RagRuleError,
)
from app.services.rag_selection_service import (
    assign_department,
    clear_qa_source,
    list_department_usage,
    set_document_usage,
    set_qa_source,
)

TAX = KnowledgeBase("kb-tax", "營業稅申報", approval_status=APPROVAL_APPROVED)
LAWS = KnowledgeBase("kb-laws", "發票與會計法規", approval_status=APPROVAL_APPROVED)
ROWS = (
    DepartmentAccess("kb-tax", "finance", use_for_qa=True),
    DepartmentAccess("kb-laws", "finance"),
    DepartmentAccess("kb-tax", "procurement", use_for_qa=True),
)


def _qa(rows: tuple[DepartmentAccess, ...], department_id: str) -> list[str]:
    return [r.knowledge_base_id for r in rows if r.department_id == department_id and r.use_for_qa]


def test_switching_qa_source_keeps_one_per_department() -> None:
    rows = set_qa_source(LAWS, ROWS, "finance")
    assert _qa(rows, "finance") == ["kb-laws"]
    assert _qa(rows, "procurement") == ["kb-tax"]


def test_qa_source_requires_approved_and_active() -> None:
    pending = KnowledgeBase("kb-laws", "發票與會計法規", approval_status=APPROVAL_PENDING)
    with pytest.raises(RagRuleError) as error:
        set_qa_source(pending, ROWS, "finance")
    assert error.value.code == "KB_NOT_APPROVED"
    inactive = KnowledgeBase("kb-laws", "x", approval_status=APPROVAL_APPROVED, is_active=False)
    with pytest.raises(RagRuleError):
        set_qa_source(inactive, ROWS, "finance")


def test_qa_source_requires_read_access() -> None:
    with pytest.raises(RagRuleError) as error:
        set_qa_source(LAWS, ROWS, "procurement")
    assert error.value.code == "NO_READ_ACCESS"
    unreadable = (DepartmentAccess("kb-laws", "finance", can_read=False),)
    with pytest.raises(RagRuleError):
        set_qa_source(LAWS, unreadable, "finance")


def test_unassigning_read_also_clears_qa() -> None:
    rows = assign_department(ROWS, "kb-tax", "finance", can_read=False)
    row = next(r for r in rows if (r.knowledge_base_id, r.department_id) == ("kb-tax", "finance"))
    assert (row.can_read, row.use_for_qa) == (False, False)


def test_assign_new_department_appends_row() -> None:
    rows = assign_department(ROWS, "kb-laws", "it", can_manage=True)
    assert DepartmentAccess("kb-laws", "it", can_read=True, can_manage=True) in rows


def test_clear_qa_source() -> None:
    assert _qa(clear_qa_source(ROWS, "finance"), "finance") == []


def test_document_usage_requires_readable_kb() -> None:
    doc = RagFile("f-1", "kb-laws", "統一發票使用辦法")
    with pytest.raises(RagRuleError):
        set_document_usage((), ROWS, doc, "procurement", True)
    rows = set_document_usage(
        (FileDepartmentUsage("f-1", "finance", True),), ROWS, doc, "finance", False, "u-1"
    )
    assert rows == (FileDepartmentUsage("f-1", "finance", False, "u-1"),)


def test_department_usage_overview() -> None:
    files = [
        RagFile("f-1", "kb-tax", "要點"),
        RagFile("f-2", "kb-tax", "附件五"),
        RagFile("f-3", "kb-laws", "x"),
    ]
    usage = (FileDepartmentUsage("f-1", "finance", True),)
    overview = list_department_usage("finance", [TAX, LAWS], ROWS, files, usage)
    assert overview.qa_source == TAX
    assert {kb.knowledge_base_id for kb in overview.readable} == {"kb-tax", "kb-laws"}
    assert [(f.rag_file_id, used) for f, used in overview.documents] == [
        ("f-1", True),
        ("f-2", False),
    ]
