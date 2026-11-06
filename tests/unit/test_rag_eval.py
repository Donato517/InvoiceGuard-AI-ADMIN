# ruff: noqa: F811
"""
模組名稱：tests.unit.test_rag_eval
功能說明：RAG 題庫回歸測試——題庫格式、寬鬆比對規則、測試頁只限資訊部、逐題執行沿用 ask_test；替身不呼叫模型。
相依模組：app.services.rag_eval_service、app.routers.knowledge
"""

from dataclasses import replace

import pytest
from test_knowledge_rag_pages import KB_ROW, Pages, pages  # noqa: F401

from app.schemas.rag import RagActor, RagRuleError
from app.schemas.rag_eval_cases import CASES, FORMAT_CONCEPT, FORMAT_STEPS, EvalCase
from app.services import rag_admin_service, rag_eval_service, rag_qa_service
from app.services.rag_qa_service import NO_ANSWER, SERVICE_UNAVAILABLE, QaAnswer, QaSource

ACT = "加值型及非加值型營業稅法"


def _answer(text: str, *titles: str, retrieved: tuple[str, ...] | None = None) -> QaAnswer:
    """合成回答；retrieved 預設等於引用的文件（檢索有找到、模型也引用）。"""
    sources = tuple(QaSource(n, title, "營業稅", 0) for n, title in enumerate(titles, start=1))
    return QaAnswer(
        text,
        grounded=bool(titles),
        sources=sources,
        total_tokens=120,
        retrieved_titles=titles if retrieved is None else retrieved,
    )


def test_case_ids_are_unique_and_expectations_valid() -> None:
    assert len({case.case_id for case in CASES}) == len(CASES) >= 15
    for case in CASES:
        assert case.question.strip()
        assert case.expected_format in ("", FORMAT_CONCEPT, FORMAT_STEPS)
        if case.no_answer:
            assert not case.sources_any and not case.keywords_any
    assert any(case.no_answer for case in CASES)


def test_passing_concept_answer() -> None:
    case = EvalCase(
        "x", "差在哪", sources_any=(ACT,), keywords_any=("非加值",), expected_format=FORMAT_CONCEPT
    )
    # 畫面上的回答已拿掉「白話說明：」標題，概念格式改看「主要差別／重點」
    result = rag_eval_service.evaluate(
        case, _answer("加值與非加值是兩種計算方式。\n主要差別：\n・非加值… [1]", ACT)
    )
    assert result.passed and [c.label for c in result.checks] == [
        "檢索命中",
        "有依文件回答",
        "引用預期文件",
        "含關鍵字",
        "概念格式",
    ]


@pytest.mark.parametrize(
    "answer,failed_label",
    [
        (
            _answer(
                "主要差別：非加值 [1]", "統一發票使用辦法", retrieved=(ACT, "統一發票使用辦法")
            ),
            "引用預期文件",
        ),
        (_answer("主要差別：只講加值 [1]", ACT), "含關鍵字"),
        (_answer("你可以這樣做：非加值 [1]", ACT), "概念格式"),
        (QaAnswer(NO_ANSWER, grounded=False, retrieved_titles=(ACT,)), "有依文件回答"),
    ],
)
def test_each_failed_check_is_reported(answer: QaAnswer, failed_label: str) -> None:
    case = EvalCase(
        "x", "差在哪", sources_any=(ACT,), keywords_any=("非加值",), expected_format=FORMAT_CONCEPT
    )
    result = rag_eval_service.evaluate(case, answer)
    assert not result.passed
    assert [c.label for c in result.checks if not c.passed] == [failed_label]


def test_out_of_scope_expects_no_answer() -> None:
    case = EvalCase("o", "尾牙", no_answer=True)
    assert rag_eval_service.evaluate(case, QaAnswer(NO_ANSWER, grounded=False)).passed
    assert not rag_eval_service.evaluate(case, _answer("編造的回答 [1]", ACT)).passed


def test_service_failure_never_passes() -> None:
    case = EvalCase("o", "尾牙", no_answer=True)
    result = rag_eval_service.evaluate(case, QaAnswer(SERVICE_UNAVAILABLE, grounded=False))
    assert not result.passed and result.checks[0].label == "服務與檢查"


def test_unknown_case_is_rejected() -> None:
    with pytest.raises(RagRuleError):
        rag_eval_service.get_case("missing")


def test_run_case_uses_all_available_knowledge_bases(monkeypatch: pytest.MonkeyPatch) -> None:
    available = replace(KB_ROW, approval_status="approved")
    pending = replace(KB_ROW, knowledge_base_id="pending-kb")
    monkeypatch.setattr(
        rag_admin_service,
        "load_overview",
        lambda: rag_admin_service.KnowledgeOverview((available, pending), ()),
    )
    seen = {}

    def ask_test(
        question: str, actor: RagActor, ids: list[str], *args: object, **kwargs: object
    ) -> QaAnswer:
        seen.update(question=question, ids=ids, **kwargs)
        return QaAnswer(NO_ANSWER, grounded=False)

    monkeypatch.setattr(rag_qa_service, "ask_test", ask_test)
    actor = RagActor("u", "d", True)
    result = rag_eval_service.run_case("out-of-scope", actor)
    assert result.passed and seen == {
        "question": "公司尾牙要辦在哪裡？",
        "ids": [KB_ROW.knowledge_base_id],
        # 題庫測試不計入查無資料次數
        "record_no_answer_stats": False,
    }


def test_eval_page_and_run_only_for_it(pages: Pages, monkeypatch: pytest.MonkeyPatch) -> None:
    assert pages.client.get("/knowledge/eval").status_code == 403
    assert pages.client.post("/knowledge/eval/run", data={"case_id": "vat-rate"}).status_code == 403

    pages.is_it = True
    html = pages.client.get("/knowledge/eval").text
    assert "題庫測試" in html and "data-eval-run-all" in html
    assert html.count("data-eval-run=") == len(CASES)

    monkeypatch.setattr(
        rag_eval_service,
        "run_case",
        lambda case_id, actor: rag_eval_service.evaluate(
            rag_eval_service.get_case(case_id), _answer("營業稅率為百分之五 [1]", ACT)
        ),
    )
    response = pages.client.post("/knowledge/eval/run", data={"case_id": "vat-rate"})
    assert response.status_code == 200
    assert "✓ 通過" in response.text and "重新執行" in response.text

    def missing(case_id: str, actor: RagActor) -> object:
        raise RagRuleError("EVAL_CASE_NOT_FOUND", "找不到這個題目")

    monkeypatch.setattr(rag_eval_service, "run_case", missing)
    response = pages.client.post("/knowledge/eval/run", data={"case_id": "x"})
    assert response.status_code == 400 and "找不到這個題目" in response.text


def test_chat_page_links_to_eval(pages: Pages) -> None:
    pages.is_it = True
    assert 'href="/knowledge/eval"' in pages.client.get("/knowledge/chat").text


# ---- 分層檢查：檢索層與回答層分開 ----


def test_retrieval_miss_is_reported_separately_from_answer() -> None:
    """檢索沒找到預期文件、模型也查無資料：兩層都報，看得出是檢索的問題。"""
    case = EvalCase("x", "稅率", sources_any=(ACT,), keywords_any=("5%",))
    result = rag_eval_service.evaluate(case, QaAnswer(NO_ANSWER, grounded=False))
    assert [(c.label, c.passed) for c in result.checks] == [
        ("檢索命中", False),
        ("有依文件回答", False),
    ]
    assert "候選段落來源：無" in result.checks[0].detail


def test_retrieval_hit_but_no_answer_points_to_answer_layer() -> None:
    """檢索有找到、模型卻判斷不足：檢索層通過，問題在回答層。"""
    case = EvalCase("x", "稅率", sources_any=(ACT,))
    answer = QaAnswer(NO_ANSWER, grounded=False, retrieved_titles=(ACT, "施行細則"))
    result = rag_eval_service.evaluate(case, answer)
    assert [(c.label, c.passed) for c in result.checks] == [
        ("檢索命中", True),
        ("有依文件回答", False),
    ]
