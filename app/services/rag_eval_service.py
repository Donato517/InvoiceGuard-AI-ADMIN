"""
模組名稱：app.services.rag_eval_service
功能說明：RAG 固定題庫回歸測試——依題目執行資訊部問答（ask_test），再以寬鬆規則比對結果。
相依模組：app.schemas.rag_eval_cases、app.services.rag_qa_service、app.services.rag_admin_service
注意事項：
    - 只限資訊部（沿用 ask_test 的資訊部檢查）；一次只跑一題，避開 60 秒限制，畫面依序呼叫。
    - 範圍為全部已核准、啟用且至少一份檔案的知識庫，不帶部門。
    - 分層檢查：「檢索命中」看送給模型的段落是否含預期文件，「引用預期文件」看回答實際引用；
      檢索沒命中＝檢索或切段問題；檢索命中但沒引用或查無資料＝回答（提示詞）問題。
    - 不保存題目執行結果；每次執行都會呼叫模型並消耗 token。
"""

from dataclasses import dataclass

from app.schemas.rag import APPROVAL_APPROVED, RagActor, RagRuleError
from app.schemas.rag_eval_cases import CASES, FORMAT_CONCEPT, FORMAT_STEPS, EvalCase
from app.services import rag_admin_service, rag_qa_service
from app.services.rag_qa_service import QaAnswer

# 格式判斷：概念格式以「白話說明」開頭的區塊，步驟格式以「你可以這樣做」
_FORMAT_MARKS = {FORMAT_CONCEPT: ("白話說明",), FORMAT_STEPS: ("你可以這樣做", "您可以這樣做")}


@dataclass(frozen=True)
class EvalCheck:
    """一項檢查結果。"""

    label: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class EvalResult:
    """一題的執行結果。"""

    case: EvalCase
    answer: QaAnswer
    checks: tuple[EvalCheck, ...]

    @property
    def passed(self) -> bool:
        """全部檢查都通過。"""
        return all(check.passed for check in self.checks)


def get_case(case_id: str) -> EvalCase:
    """依代碼取題目。

    Raises:
        RagRuleError: 題目不存在。
    """
    for case in CASES:
        if case.case_id == case_id:
            return case
    raise RagRuleError("EVAL_CASE_NOT_FOUND", "找不到這個題目")


def is_no_answer(answer: QaAnswer) -> bool:
    """「查無資料」：沒有依段落產生回答，也不是服務失敗或未通過檢查。"""
    return not answer.grounded and not answer.failed


def evaluate(case: EvalCase, answer: QaAnswer) -> EvalResult:
    """以寬鬆規則比對回答；只回傳結果，不呼叫任何服務。

    Args:
        case: 題目與預期。
        answer: 問答結果。

    Returns:
        各項檢查與是否通過。
    """
    if answer.failed:
        return EvalResult(
            case, answer, (EvalCheck("服務與檢查", False, "服務失敗或回答未通過檢查"),)
        )
    if case.no_answer:
        return EvalResult(
            case,
            answer,
            (
                EvalCheck(
                    "應查無資料",
                    is_no_answer(answer),
                    "" if is_no_answer(answer) else "卻產生了回答",
                ),
            ),
        )
    # 分層檢查：先看檢索有沒有找到預期文件，再看模型是否依段落回答，錯誤原因才分得清楚
    checks = []
    if case.sources_any:
        found = list(answer.retrieved_titles)
        hit = any(expected in title for title in found for expected in case.sources_any)
        checks.append(
            EvalCheck("檢索命中", hit, "" if hit else f"候選段落來源：{'、'.join(found) or '無'}")
        )
    if not answer.grounded:
        # 查無資料時回答層的檢查沒有意義，只回報這一項（檢索命中仍保留，可看出是哪一層的問題）
        checks.append(EvalCheck("有依文件回答", False, "查無資料"))
        return EvalResult(case, answer, tuple(checks))
    checks.append(EvalCheck("有依文件回答", True))
    if case.sources_any:
        titles = [source.file_title for source in answer.sources]
        hit = any(expected in title for title in titles for expected in case.sources_any)
        checks.append(
            EvalCheck("引用預期文件", hit, "" if hit else f"實際引用：{'、'.join(titles) or '無'}")
        )
    if case.keywords_any:
        hit = any(keyword in answer.text for keyword in case.keywords_any)
        checks.append(
            EvalCheck("含關鍵字", hit, "" if hit else f"需含其一：{'、'.join(case.keywords_any)}")
        )
    if case.expected_format:
        marks = _FORMAT_MARKS[case.expected_format]
        hit = any(mark in answer.text for mark in marks)
        label = "概念格式" if case.expected_format == FORMAT_CONCEPT else "步驟格式"
        checks.append(EvalCheck(label, hit, "" if hit else f"缺少「{marks[0]}」"))
    return EvalResult(case, answer, tuple(checks))


def eval_knowledge_base_ids() -> list[str]:
    """題庫測試的範圍：全部已核准、啟用且至少一份檔案的知識庫。"""
    overview = rag_admin_service.load_overview()
    return sorted(
        kb.knowledge_base_id
        for kb in overview.knowledge_bases
        if kb.approval_status == APPROVAL_APPROVED and kb.is_active and kb.file_count >= 1
    )


def run_case(case_id: str, actor: RagActor) -> EvalResult:
    """執行一題並比對。

    Raises:
        RagRuleError: 題目不存在、不是資訊部或沒有可用的知識庫。
    """
    case = get_case(case_id)
    ids = eval_knowledge_base_ids()
    if not ids:
        raise RagRuleError("KNOWLEDGE_REQUIRED", "沒有已核准、啟用且有檔案的知識庫可以測試")
    # 題庫測試不計入查無資料次數
    return evaluate(
        case, rag_qa_service.ask_test(case.question, actor, ids, record_no_answer_stats=False)
    )
