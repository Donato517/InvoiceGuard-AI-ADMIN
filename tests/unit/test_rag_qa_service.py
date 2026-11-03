"""
模組名稱：tests.unit.test_rag_qa_service
功能說明：RAG 問答防幻覺測試——找不到相關段落不呼叫模型、模型回答沒有引用或引用不存在時改回固定訊息、
          資訊部才能指定知識庫測試、服務錯誤時的固定訊息、問題長度限制。
相依模組：app.services.rag_qa_service
注意事項：
    - 向量、對話與 DB 皆用替身，不呼叫正式 DB、Google 或 Groq。
"""

import inspect
import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest

from app.integrations.chat_models import ChatMessage, ChatReply
from app.integrations.gemini import GeminiError
from app.integrations.groq import GroqError
from app.integrations.vertex_embedding import EmbeddingBatch, EmbeddingError
from app.schemas.rag import DepartmentOption, KnowledgeBase, RagActor, RagRuleError, SearchHit
from app.services import rag_qa_service as qa

FINANCE = RagActor("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-00000000000f")
IT = RagActor("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-00000000000e", True)
KB = "11111111-1111-4111-8111-111111111111"
CONTACT = "財務部"
# 截止時間相減有浮點誤差，比對剩餘秒數時要容許
TIME_EPSILON = 1e-6


def hit(distance: float, n: int = 0) -> SearchHit:
    return SearchHit(
        "第十二條 三聯式發票遺失，應取得影本並蓋章。",
        distance,
        "統一發票使用辦法",
        1,
        n,
        "營業稅",
        "f1",
    )


class FakeEmbedding:
    def __enter__(self) -> "FakeEmbedding":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def embed_texts(self, texts: Sequence[str], **_: Any) -> EmbeddingBatch:
        return EmbeddingBatch(vectors=tuple((0.0,) * 4 for _ in texts), model="m", dimensions=4)


class FakeChat:
    def __init__(self, text: str = "", error: GroqError | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[Sequence[ChatMessage], dict[str, Any]]] = []
        self.understanding = '{"need": "", "queries": []}'
        self.understanding_error: GroqError | None = None
        self.finish_reason = "stop"
        self.replies: list[str] = []

    @property
    def answer_calls(self) -> list[tuple[Sequence[ChatMessage], dict[str, Any]]]:
        return [call for call in self.calls if call[1]["purpose"] == "rag"]

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatReply:
        self.calls.append((messages, kwargs))
        if kwargs.get("purpose") == "default":
            if self.understanding_error:
                raise self.understanding_error
            return ChatReply(self.understanding, "20b", "stop", 1, 1, 2, {})
        if self.error:
            raise self.error
        if self.replies:
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return ChatReply(reply, "m", self.finish_reason, 10, 20, 30, {})
        return ChatReply(self.text, "m", self.finish_reason, 10, 20, 30, {})


class FakeFallback:
    def __init__(
        self, text: str = "", error: GeminiError | None = None, finish_reason: str = "STOP"
    ) -> None:
        self.text = text
        self.error = error
        self.finish_reason = finish_reason
        self.calls: list[tuple[Sequence[ChatMessage], dict[str, Any]]] = []

    def __enter__(self) -> "FakeFallback":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatReply:
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return ChatReply(self.text, "flash", self.finish_reason, 10, 20, 30, {})


class FakeSession:
    def close(self) -> None:
        return None

    def commit(self) -> None:
        pytest.fail("測試問答不可 commit")


@pytest.fixture(autouse=True)
def active_departments(monkeypatch: pytest.MonkeyPatch) -> list[DepartmentOption]:
    departments = [
        DepartmentOption("d1", "finance_d", CONTACT),
        DepartmentOption("d2", "technology_d", "資訊部"),
    ]
    monkeypatch.setattr(qa.rag_repository, "list_departments", lambda db: departments)
    by_id = {department.department_id: department for department in departments}
    monkeypatch.setattr(qa.rag_repository, "get_department", lambda db, id: by_id.get(id))
    return departments


@pytest.fixture
def review_hits(monkeypatch: pytest.MonkeyPatch) -> list[SearchHit]:
    hits: list[SearchHit] = []
    monkeypatch.setattr(
        qa.rag_repository, "search_review_chunks", lambda db, vec, model, kb, k: hits
    )
    monkeypatch.setattr(
        qa.rag_repository,
        "get_knowledge_base",
        lambda db, kb: KnowledgeBase(kb, "測試知識庫", "approved", file_count=1),
    )
    return hits


def _ask(
    question: str,
    chat: FakeChat,
    actor: RagActor = IT,
    kb: str | None = KB,
    fallback: FakeFallback | None = None,
) -> qa.QaAnswer:
    backup = fallback or FakeFallback(error=GeminiError("gemini_not_configured"))
    kwargs = {
        "embedding_factory": FakeEmbedding,
        "chat_factory": lambda: chat,
        "open_session": FakeSession,
        "fallback_factory": lambda: backup,
    }
    return (
        qa.ask(question, actor, **kwargs)
        if kb is None
        else qa.ask_test(question, actor, [kb], **kwargs)
    )


def test_no_relevant_passage_does_not_call_answer_model(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.8))
    chat = FakeChat("隨便回答")

    answer = _ask("發票遺失怎麼辦？", chat)

    assert answer.text == qa.NO_ANSWER and not answer.grounded
    assert chat.answer_calls == []
    assert [call[1]["purpose"] for call in chat.calls] == ["default"]


def test_grounded_answer_keeps_cited_sources(review_hits: list[SearchHit]) -> None:
    review_hits.extend([hit(0.2, 0), hit(0.3, 1)])
    chat = FakeChat("可以喔！發票遺失時要取得影本並蓋章 [1]。")

    answer = _ask("發票遺失怎麼辦？", chat)

    assert answer.grounded and answer.text.endswith(qa.CLOSING.format(contact=CONTACT))
    assert [s.number for s in answer.sources] == [1]
    assert answer.total_tokens == 60
    messages, kwargs = chat.answer_calls[0]
    assert "[1] 知識庫「營業稅」《統一發票使用辦法》" in messages[0].text
    assert kwargs["purpose"] == "rag" and kwargs["system_instruction"] == qa.SYSTEM_PROMPT


@pytest.mark.parametrize(
    "reply,message",
    [
        ("發票遺失要補辦。", qa.UNVERIFIED_ANSWER),
        ("請參考 [3]。", qa.UNVERIFIED_ANSWER),
        (qa._NO_INFO_MARK, qa.NO_ANSWER),
    ],
    ids=["none", "bad", "mark"],
)
def test_answer_without_valid_citation_is_discarded(
    review_hits: list[SearchHit], reply: str, message: str
) -> None:
    review_hits.append(hit(0.2))

    answer = _ask("發票遺失怎麼辦？", FakeChat(reply))

    assert answer.text == message.format(contact=CONTACT)
    assert not answer.grounded and answer.sources == ()


@pytest.mark.parametrize(
    "reply",
    [
        "適用這個情況 [1]\n你可以這樣做：\n1、先取得影本 [1]\n2、再交給對方",
        "適用這個情況\n你可以這樣做：\n1. 先取得影本\n2. 再交給對方\n\n依據：第五條 [1]",
    ],
)
def test_numbered_step_without_citation_is_discarded(
    review_hits: list[SearchHit], reply: str
) -> None:
    review_hits.append(hit(0.2))

    answer = _ask("發票遺失怎麼辦？", FakeChat(reply))

    assert answer.text == qa.UNVERIFIED_ANSWER.format(contact=CONTACT)
    assert not answer.grounded and answer.sources == ()
    assert "個步驟沒有來源" in (answer.note or "")


@pytest.mark.parametrize(
    "reply",
    [
        "你可以這樣做：\n1、先取得影本 [1]\n2、再交給對方 [1]\n\n依據：第十二條 [1]",
        "你可以這樣做：\n1、先取得影本，\n   影本要蓋章 [1]\n2、再交給對方 [1]",
        "這次資料只能確認第 1 種情況 [1]",
    ],
)
def test_answer_with_cited_steps_passes(review_hits: list[SearchHit], reply: str) -> None:
    review_hits.append(hit(0.2))

    assert _ask("發票遺失怎麼辦？", FakeChat(reply)).grounded


@pytest.mark.parametrize(
    "departments,expected",
    [
        ([("finance_d", "財務部")], "財務部"),
        ([("accounting_d", "會計部"), ("finance_d", "財務部")], "會計部或財務部"),
        ([("finance_d", "Treasury"), ("technology_d", "資訊部")], "Treasury"),
        ([("technology_d", "資訊部")], qa.DEFAULT_CONTACT),
        ([], qa.DEFAULT_CONTACT),
    ],
)
def test_contact_comes_from_active_departments(
    departments: list[tuple[str, str]], expected: str
) -> None:
    options = [DepartmentOption(str(n), *item) for n, item in enumerate(departments)]
    assert qa.contact_name(options) == expected


def test_no_answer_never_names_a_department(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))

    answer = _ask("公司電腦壞了要找誰修？", FakeChat("NO_ANSWER"))

    assert answer.text == qa.NO_ANSWER and CONTACT not in answer.text


@pytest.mark.parametrize("reply", ["沒有引用", "取得影本 [1]"])
def test_new_department_appears_in_fixed_messages_without_code_change(
    review_hits: list[SearchHit], active_departments: list[DepartmentOption], reply: str
) -> None:
    active_departments.insert(0, DepartmentOption("d3", "accounting_d", "會計部"))
    review_hits.append(hit(0.2))

    assert "會計部或財務部" in _ask("發票遺失怎麼辦？", FakeChat(reply)).text


def test_prompts_and_fixed_messages_are_not_tailored() -> None:
    general = (
        qa.SYSTEM_PROMPT,
        qa.UNDERSTAND_SYSTEM_PROMPT,
        qa.REVIEW_RULES,
        qa.NO_ANSWER,
        qa.UNVERIFIED_ANSWER,
        qa.CLOSING,
    )
    for word in ("遺失", "開錯", "退貨", "作廢", "重開", "財務部", "會計部"):
        assert not any(word in content for content in general)


def test_only_it_can_test_specific_knowledge_base() -> None:
    with pytest.raises(RagRuleError) as error:
        _ask("問題", FakeChat(), actor=FINANCE)

    assert error.value.code == "NOT_IT_REVIEWER"


def test_department_question_uses_four_condition_search(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def search(
        text: str, user: Any, dept: Any, top_k: int, client: Any, open_session: Any, **kwargs: Any
    ) -> Any:
        seen.append((text, str(dept)))
        return qa.retrieval.SearchResult(hits=(hit(0.1),), sources=())

    monkeypatch.setattr(qa.retrieval, "search", search)

    answer = _ask("發票遺失？", FakeChat("要取得影本 [1]"), actor=FINANCE, kb=None)

    assert answer.grounded and seen == [("發票遺失？", FINANCE.department_id)]


def test_department_question_understands_and_searches_with_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """部門問答（ask）與資訊部測試同流程：帶入目前部門名稱、理解問題、多查詢檢索、依題型回答。"""
    monkeypatch.setattr(
        qa.rag_repository,
        "get_department",
        lambda db, id: (
            DepartmentOption(id, "finance_d", CONTACT) if id == FINANCE.department_id else None
        ),
    )
    seen: dict[str, Any] = {}

    def search(*args: Any, **kwargs: Any) -> Any:
        seen["extra"] = kwargs["extra_queries"]
        return qa.retrieval.SearchResult(hits=(hit(0.1),), sources=())

    monkeypatch.setattr(qa.retrieval, "search", search)
    chat = FakeChat("白話說明：\n一句常識。\n主要差別：\n・甲 [1]\n依據：\n・文件 [1]")
    chat.understanding = (
        '{"type":"comparison","concepts":["甲","乙"],"need":"想知道差別",'
        '"queries":["甲的定義","乙的定義"]}'
    )

    answer = _ask("甲和乙差在哪？", chat, actor=FINANCE, kb=None)

    assert answer.grounded and seen["extra"] == ("甲的定義", "乙的定義")
    understand, *answers = chat.calls
    assert understand[0][0].text == f"【提問部門】{CONTACT}\n甲和乙差在哪？"
    prompt = answers[0][0][0].text
    assert "【題型】比較題（問兩者差在哪）" in prompt and f"【提問部門】{CONTACT}" in prompt


def test_department_question_without_department_row_still_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        qa.retrieval, "search", lambda *args, **kwargs: qa.retrieval.SearchResult((hit(0.1),), ())
    )
    chat = FakeChat("規定 [1]")

    answer = _ask("問題", chat, FINANCE, None)

    assert answer.grounded
    assert "【提問部門】" not in chat.answer_calls[0][0][0].text


def test_department_no_answer_names_department_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        qa.retrieval, "search", lambda *args, **kwargs: qa.retrieval.SearchResult((), ())
    )
    chat = FakeChat()
    chat.understanding = '{"need":"想知道差別","queries":[]}'

    answer = _ask("問題", chat, FINANCE, None)

    assert "目前部門可用的知識庫" in answer.text and "「」" not in answer.text


def test_service_errors_return_fixed_message(
    review_hits: list[SearchHit], monkeypatch: pytest.MonkeyPatch
) -> None:
    review_hits.append(hit(0.1))
    assert _ask("問題", FakeChat(error=GroqError("groq_timeout"))).text == qa.SERVICE_UNAVAILABLE

    class BrokenEmbedding(FakeEmbedding):
        def embed_texts(self, texts: Sequence[str], **_: Any) -> EmbeddingBatch:
            raise EmbeddingError("embedding_timeout")

    answer = qa.ask_test(
        "問題",
        IT,
        [KB],
        embedding_factory=BrokenEmbedding,
        open_session=FakeSession,
        chat_factory=FakeChat,
    )
    assert answer.text == qa.SERVICE_UNAVAILABLE


@pytest.mark.parametrize("question", ["   ", "長" * (qa.MAX_QUESTION_LENGTH + 1)])
def test_question_must_be_present_and_short(question: str) -> None:
    with pytest.raises(RagRuleError):
        _ask(question, FakeChat())


def test_truncated_reply_is_discarded_with_note(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat("思考中…")
    chat.finish_reason = "length"

    answer = _ask(
        "發票遺失怎麼辦？", chat, fallback=FakeFallback("被截斷", finish_reason="MAX_TOKENS")
    )

    assert answer.text == qa.UNVERIFIED_ANSWER.format(contact=CONTACT)
    assert "截斷" in (answer.note or "") and "已改用備援模型" in (answer.note or "")


@pytest.mark.parametrize(
    "error,finish_reason,cause",
    [
        (GroqError("groq_quota_exceeded", "rate_limit_exceeded"), "stop", "groq_quota_exceeded"),
        (GroqError("groq_timeout"), "stop", "groq_timeout"),
        (GroqError("groq_invalid_input", "output_parse_failed"), "stop", "output_parse_failed"),
        (GroqError("groq_failed", "stop"), "stop", "groq_failed"),
        (None, "length", "groq_truncated"),
    ],
    ids=["quota", "timeout", "invalid", "blank", "truncated"],
)
def test_groq_failure_falls_back_silently(
    review_hits: list[SearchHit], error: GroqError | None, finish_reason: str, cause: str
) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat("被截斷", error=error)
    chat.finish_reason = finish_reason
    fallback = FakeFallback("取得影本 [1]")

    answer = _ask("發票遺失怎麼辦？", chat, fallback=fallback)

    assert answer.grounded and answer.text.startswith("取得影本 [1]")
    assert "備援" not in answer.text and "Groq" not in answer.text
    assert [source.number for source in answer.sources] == [1]
    assert "已改用備援模型" in (answer.note or "") and cause in (answer.note or "")
    messages, kwargs = fallback.calls[0]
    assert messages == chat.answer_calls[0][0]
    assert kwargs["system_instruction"] == chat.answer_calls[0][1]["system_instruction"]
    assert kwargs["max_tokens"] == qa.ANSWER_MAX_TOKENS
    assert 0 < kwargs["timeout_seconds"] <= qa.ROUND_TIMEOUT_SECONDS


def test_second_round_replaces_draft_and_receives_evidence(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat()
    chat.replies = ["初稿寫錯來源 [1]", "修正後的回答 [1]"]

    answer = _ask("發票遺失怎麼辦？", chat)

    assert answer.grounded and answer.text.startswith("修正後的回答 [1]")
    assert "初稿" not in answer.text and answer.total_tokens == 60
    first, second = chat.answer_calls
    assert first[1]["system_instruction"] == qa.SYSTEM_PROMPT
    assert second[1]["system_instruction"].endswith(qa.REVIEW_RULES)
    assert "【參考段落】" in second[0][0].text and "【問題】" in second[0][0].text
    assert "【初稿】\n初稿寫錯來源 [1]" in second[0][0].text
    assert "【程式檢查發現的問題】" not in second[0][0].text


def test_second_round_repairs_what_the_program_check_found(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat()
    chat.replies = ["1、取得影本\n2、交給對方 [1]", "1、取得影本 [1]\n2、交給對方 [1]"]

    answer = _ask("發票遺失怎麼辦？", chat)

    assert answer.grounded
    assert "【程式檢查發現的問題】\n回答有 1 個步驟沒有來源" in chat.answer_calls[1][0][0].text


@pytest.mark.parametrize("draft", ["初稿 [1]", "沒有來源"])
def test_unreviewed_draft_is_never_shown(review_hits: list[SearchHit], draft: str) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat()
    chat.replies = [draft, GroqError("groq_quota_exceeded")]

    answer = _ask("發票遺失怎麼辦？", chat)

    assert answer.text == qa.SERVICE_UNAVAILABLE and not answer.grounded
    assert "第二輪未完成" in (answer.note or "")


def test_second_round_failing_checks_shows_fixed_message(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat()
    chat.replies = ["初稿 [1]", "第二輪沒有來源"]

    answer = _ask("發票遺失怎麼辦？", chat)

    assert answer.text == qa.UNVERIFIED_ANSWER.format(contact=CONTACT) and not answer.grounded


def test_second_round_can_conclude_no_answer(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat()
    chat.replies = ["初稿 [1]", "NO_ANSWER"]

    assert _ask("發票遺失怎麼辦？", chat).text == qa.NO_ANSWER


def test_no_answer_draft_skips_second_round(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat("NO_ANSWER")

    _ask("公司電腦壞了要找誰修？", chat)

    assert len(chat.answer_calls) == 1


def test_no_time_left_skips_second_round(monkeypatch: pytest.MonkeyPatch) -> None:
    chat = FakeChat("初稿 [1]")
    now = qa.time.monotonic()

    answer = qa.compose_answer("問題", [hit(0.2)], chat, deadline=now + 1)

    assert answer.text == qa.SERVICE_UNAVAILABLE and not answer.grounded
    assert "未完成第二輪檢查" in (answer.note or "")
    assert len(chat.answer_calls) == 1


def test_rounds_never_run_past_the_question_deadline() -> None:
    chat = FakeChat("初稿 [1]")
    now = qa.time.monotonic()

    qa.compose_answer("問題", [hit(0.2)], chat, deadline=now + 9)

    assert all(call[1]["timeout_seconds"] <= 9 + TIME_EPSILON for call in chat.answer_calls)


class SlowClock:
    """每次呼叫 chat 後把時鐘往前推，模擬回覆比預算晚到。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
        self.now = 1000.0
        self.seconds = seconds
        monkeypatch.setattr(qa.time, "monotonic", lambda: self.now)

    def wrap(self, client: Any) -> Any:
        original = client.chat

        def chat(messages: Sequence[ChatMessage], **kwargs: Any) -> ChatReply:
            self.now += self.seconds
            return original(messages, **kwargs)

        client.chat = chat
        return client


def test_late_fallback_reply_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = SlowClock(monkeypatch, 30)
    fallback = clock.wrap(FakeFallback("回答 [1]"))
    chat = FakeChat(error=GroqError("groq_quota_exceeded"))

    answer = qa.compose_answer(
        "問題", [hit(0.2)], chat, fallback_factory=lambda: fallback, deadline=clock.now + 56
    )

    assert answer.text == qa.SERVICE_UNAVAILABLE and "逾時" in (answer.note or "")


def test_late_groq_reply_moves_to_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = SlowClock(monkeypatch, 30)
    chat = clock.wrap(FakeChat("回答 [1]"))
    fallback = FakeFallback("備援回答 [1]")

    answer = qa.compose_answer(
        "問題", [hit(0.2)], chat, fallback_factory=lambda: fallback, deadline=clock.now + 56
    )

    assert not answer.grounded and "groq_late" in (answer.note or "")


def test_understanding_and_embedding_share_the_question_deadline(
    review_hits: list[SearchHit], monkeypatch: pytest.MonkeyPatch
) -> None:
    review_hits.append(hit(0.2))
    budgets: list[float] = []

    class Embedding(FakeEmbedding):
        def embed_texts(self, texts: Sequence[str], **kwargs: Any) -> EmbeddingBatch:
            budgets.append(kwargs["timeout_seconds"])
            return super().embed_texts(texts, **kwargs)

    monkeypatch.setattr(qa, "QA_TOTAL_SECONDS", 3.0)
    chat = FakeChat("回答 [1]")
    qa.ask_test(
        "問題",
        IT,
        [KB],
        embedding_factory=Embedding,
        chat_factory=lambda: chat,
        open_session=FakeSession,
    )

    assert chat.calls[0][1]["timeout_seconds"] <= 3.0 + TIME_EPSILON
    assert budgets[0] <= 3.0 + TIME_EPSILON


def test_groq_client_failure_still_uses_fallback(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    fallback = FakeFallback("備援回答 [1]")

    def no_groq() -> FakeChat:
        raise GroqError("groq_not_configured")

    answer = qa.ask_test(
        "問題",
        IT,
        [KB],
        embedding_factory=FakeEmbedding,
        chat_factory=no_groq,
        open_session=FakeSession,
        fallback_factory=lambda: fallback,
    )

    assert answer.grounded and answer.text.startswith("備援回答 [1]")
    assert len(fallback.calls) == 2 and "groq_not_configured" in (answer.note or "")


def test_quota_failure_skips_groq_in_second_round(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat(error=GroqError("groq_quota_exceeded"))
    fallback = FakeFallback("備援回答 [1]")

    _ask("問題", chat, fallback=fallback)

    assert len(chat.answer_calls) == 1 and len(fallback.calls) == 2


def test_quota_word_in_other_error_does_not_skip_groq(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat()
    chat.replies = [GroqError("groq_invalid_input", "not_groq_quota_exceeded"), "修正 [1]"]

    _ask("問題", chat, fallback=FakeFallback("備援 [1]"))

    assert len(chat.answer_calls) == 2


def test_late_understanding_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = SlowClock(monkeypatch, 2)
    chat = clock.wrap(FakeChat())
    chat_text = '{"need":"目的","queries":["查詢"]}'
    original = FakeChat()
    original.understanding = chat_text
    chat = clock.wrap(original)

    result = qa.understand_question("問題", None, chat, deadline=clock.now + 1)

    assert result.failure == "late" and result.need == ""


def test_ask_gives_embedding_only_the_remaining_time(
    review_hits: list[SearchHit], monkeypatch: pytest.MonkeyPatch
) -> None:
    budgets: list[float] = []

    def search(*args: Any, **kwargs: Any) -> Any:
        budgets.append(kwargs["timeout_seconds"])
        return qa.retrieval.SearchResult(hits=(), sources=())

    monkeypatch.setattr(qa.retrieval, "search", search)
    monkeypatch.setattr(qa, "QA_TOTAL_SECONDS", 2.0)

    _ask("問題", FakeChat("回答 [1]"), actor=IT, kb=None)

    assert budgets[0] <= 2.0 + TIME_EPSILON


def test_exhausted_deadline_after_retrieval_is_a_service_failure(
    review_hits: list[SearchHit], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qa, "QA_TOTAL_SECONDS", 0.0)
    monkeypatch.setattr(
        qa.retrieval, "search", lambda *args, **kwargs: qa.retrieval.SearchResult((hit(0.1),), ())
    )

    answer = _ask("問題", FakeChat("回答 [1]"), actor=IT, kb=None)

    # 部門問答也先理解問題；時間用完時理解結果不採用，並在測試說明註記
    assert answer.text == qa.SERVICE_UNAVAILABLE
    assert answer.note and answer.note.startswith("超過整題時限")


def test_other_groq_failure_still_tries_groq_in_second_round(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat()
    chat.replies = [GroqError("groq_timeout"), "修正 [1]"]

    answer = _ask("問題", chat, fallback=FakeFallback("備援 [1]"))

    assert len(chat.answer_calls) == 2 and answer.text.startswith("修正 [1]")


def test_unsafe_failure_detail_never_reaches_the_note(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat(error=GroqError("groq_quota_exceeded"))
    fallback = FakeFallback(error=GeminiError("gemini_blocked", "任意 內容 <script>"))

    answer = _ask("問題", chat, fallback=fallback)

    assert "gemini_blocked" in (answer.note or "") and "任意" not in (answer.note or "")


@pytest.mark.parametrize(
    "reply",
    [
        "你可以這樣做：\n1) 取得影本\n2) 交給對方\n\n依據：第十二條 [1]",
        "你可以這樣做：\n1、取得影本 [1]\n2、交給對方\n依據：第十二條 [1]",
        "您可以這樣做：\n1、取得影本 [1]\n\n再交給對方。\n\n依據：第十二條 [1]",
        "1、取得影本\n2、交給對方 [1]",
    ],
)
def test_uncited_operation_is_detected(reply: str) -> None:
    assert qa.uncited_steps(reply) >= 1


@pytest.mark.parametrize(
    "reply",
    [
        "你可以這樣做：\n如果是紙本：\n1、取得影本 [1]\n\n如果是電子：\n1、列印 [1]\n\n依據：第十二條 [1]",
        "你可以這樣做：\n1、先決定類型。\n   - 紙本要蓋章 [1]\n\n2、填寫項目 [1]",
        "適用這個情況，資料不足以確認 [1]。\n\n目前還需要確認：\n・客戶身分",
        "資料只能確認第 1 種情況 [1]",
    ],
)
def test_cited_operation_is_accepted(reply: str) -> None:
    assert qa.uncited_steps(reply) == 0


@pytest.mark.parametrize("reply", ["取得影本 [1]，另見 [100]", "1、操作甲 [1][1000]"])
def test_citation_out_of_range_is_rejected(reply: str) -> None:
    assert qa.check_citations(reply, 1) is None


@pytest.mark.parametrize(
    "reply",
    [
        "1、執行操作甲 [1]\n\n另外必須執行操作乙。",
        "・操作甲\n・操作乙\n\n依據：[1]",
    ],
)
def test_uncited_operation_without_heading_is_detected(reply: str) -> None:
    assert qa.uncited_steps(reply) >= 1


def test_indented_sub_list_belongs_to_its_step() -> None:
    reply = "你可以這樣做：\n1、備妥以下資料 [1]\n   1) 文件甲\n   2) 文件乙\n2、送交 [1]"
    assert qa.uncited_steps(reply) == 0


def test_explanation_first_paragraph_needs_no_citation() -> None:
    assert qa.uncited_steps("這個回答適用於一般情況。\n\n1、操作甲 [1]") == 0


@pytest.mark.parametrize(
    "reply,wrong",
    [
        ("依第九十九條取得影本 [1]", ["第99條"]),
        ("依第12條之1取得影本 [1]", ["第12條之1"]),
        ("依第 12-1 條取得影本 [1]", ["第12條之1"]),
        ("依第十二條取得影本 [1]", []),
        ("依第12條取得影本 [1]", []),
        ("第九十九條沒有標來源", []),
        ("依第99條處理。\n[1]", ["第99條"]),
        ("目前還需要確認：\n・目前無法確認第99條的內容 [1]", []),
        ("目前還需要確認：\n・缺資料\n\n依據：\n・第99條 [1]", ["第99條"]),
    ],
)
def test_article_must_appear_in_cited_passage(reply: str, wrong: list[str]) -> None:
    assert qa.unsupported_articles(reply, [hit(0.2)]) == wrong


def _article_hit(content: str, number: int) -> SearchHit:
    return replace(hit(0.1, number), content=content)


def test_swapped_sources_are_detected() -> None:
    hits = [_article_hit("第32條 甲", 0), _article_hit("第33條 乙", 1)]
    assert qa.unsupported_articles("第32條 [1]，第33條 [2]", hits) == []
    assert qa.unsupported_articles("第32條 [2]，第33條 [1]", hits) == ["第32條", "第33條"]


def test_article_written_after_its_source_is_matched() -> None:
    hits = [_article_hit("第32條 甲", 0), _article_hit("第33條 乙", 1)]
    assert qa.unsupported_articles("開立 [1]第32條第1項\n\n保存 [2]第33條", hits) == []


TWO_ARTICLES = [_article_hit("第32條 甲", 0), _article_hit("第33條 乙", 1)]


def test_multi_line_step_uses_its_own_source() -> None:
    reply = (
        "你可以這樣做：\n1、依第32條核對文件甲 [1]\n"
        "2、依第33條核對文件乙，\n   逐項確認名稱及日期 [2]"
    )
    assert qa.uncited_steps(reply) == 0 and qa.unsupported_articles(reply, TWO_ARTICLES) == []


def test_basis_list_with_source_first_is_matched_per_entry() -> None:
    reply = "依據：\n・[1]《文件甲》第32條\n・[2]《文件乙》第33條"
    assert qa.unsupported_articles(reply, TWO_ARTICLES) == []


def test_step_with_cited_sub_list_uses_its_own_source() -> None:
    reply = "1、依第32條核對：\n   - 文件甲 [1]\n2、依第33條核對：\n   - 文件乙 [2]"
    assert qa.unsupported_articles(reply, TWO_ARTICLES) == []


@pytest.mark.parametrize(
    "reply,wrong",
    [
        ("1、依第32條核對 [2]\n2、依第33條核對 [1]", ["第32條", "第33條"]),
        ("1、依第32條核對，\n   確認內容 [2]\n2、依第33條核對 [2]", ["第32條"]),
        ("依據：\n・[2]《文件甲》第32條\n・[1]《文件乙》第33條", ["第32條", "第33條"]),
    ],
)
def test_swapped_sources_across_steps_are_still_detected(reply: str, wrong: list[str]) -> None:
    assert qa.unsupported_articles(reply, TWO_ARTICLES) == wrong


def test_grouped_sources_may_cover_the_article() -> None:
    hits = [_article_hit("第32條 甲", 0), _article_hit("第33條 乙", 1)]
    assert qa.unsupported_articles("第33條 [1][2]", hits) == []
    assert qa.unsupported_articles("第33條 [1]、[2]", hits) == []


def test_digit_by_digit_chinese_numeral_is_read_positionally() -> None:
    assert qa.unsupported_articles("第一〇二條 [1]", [_article_hit("第102條 甲", 0)]) == []


def test_wrong_article_is_sent_back_for_second_round(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat()
    chat.replies = ["依第九十九條取得影本 [1]", "依第十二條取得影本 [1]"]

    answer = _ask("問題", chat)

    assert answer.grounded
    assert "第99條不在標註的段落" in chat.answer_calls[1][0][0].text


def test_internal_item_marker_is_not_shown(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))

    answer = _ask("問題", FakeChat("依據：〔第12條第3項〕 [1]"))

    assert answer.grounded and "〔" not in answer.text and "第12條第3項 [1]" in answer.text


def test_source_number_rule_is_general(review_hits: list[SearchHit]) -> None:
    assert "來源編號必須是實際寫有該內容的段落" in qa.SYSTEM_PROMPT
    assert "標註的段落必須真的含有那一條" in qa.REVIEW_RULES


def test_fallback_budget_never_exceeds_round_limit_by_float_error(
    review_hits: list[SearchHit], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 這個時間點 (t + 25.0) - t 會得到 25.000000000000057
    monkeypatch.setattr(qa.time, "monotonic", lambda: 500.00000000000017)
    fallback = FakeFallback("備援回答 [1]")
    chat = FakeChat(error=GroqError("groq_quota_exceeded"))

    qa.compose_answer("問題", [hit(0.2)], chat, fallback_factory=lambda: fallback)

    assert all(call[1]["timeout_seconds"] <= qa.ROUND_TIMEOUT_SECONDS for call in fallback.calls)


def test_healthy_groq_never_calls_fallback(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    fallback = FakeFallback("不該呼叫 [1]")

    answer = _ask("發票遺失怎麼辦？", FakeChat("取得影本 [1]"), fallback=fallback)

    assert answer.grounded and fallback.calls == []


@pytest.mark.parametrize(
    "reply,message",
    [("沒有引用", qa.UNVERIFIED_ANSWER), ("1、取得影本\n2、交給對方 [1]", qa.UNVERIFIED_ANSWER)],
)
def test_fallback_reply_passes_the_same_checks(
    review_hits: list[SearchHit], reply: str, message: str
) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat(error=GroqError("groq_quota_exceeded"))

    answer = _ask("發票遺失怎麼辦？", chat, fallback=FakeFallback(reply))

    assert answer.text == message.format(contact=CONTACT) and not answer.grounded


def test_fallback_no_answer_keeps_understood_message(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat(error=GroqError("groq_quota_exceeded"))
    chat.understanding = '{"need": "確認維修窗口", "queries": []}'

    answer = _ask("問題", chat, fallback=FakeFallback("NO_ANSWER"))

    assert answer.text.startswith("你想了解的應該是：確認維修窗口")
    assert "已改用備援模型" in (answer.note or "")


def test_full_width_citation_is_accepted_and_normalized(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))

    answer = _ask("發票遺失怎麼辦？", FakeChat("要取得影本並蓋章【1】"))

    assert answer.grounded and "[1]" in answer.text


def test_far_passages_note_shows_best_distance(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.55))
    chat = FakeChat()
    chat.understanding = '{"need":"目的","queries":[]}'

    answer = _ask("今天天氣如何？", chat)

    assert answer.note == f"最相近段落距離 0.55，超過門檻 {qa.MAX_DISTANCE}"


# ---- 章節條文比對 ----


def test_reference_groups_pair_chapter_with_each_section_and_convert_numerals() -> None:
    groups = qa.reference_groups("第四章第一節和第 二 節，還有第14條")

    assert groups == [
        ["第(4|四)章", "第(1|一)節"],
        ["第(4|四)章", "第(2|二)節"],
        ["第(14|十四)條"],
    ]


def test_question_without_reference_has_no_groups() -> None:
    assert qa.reference_groups("加值型和非加值型差在哪裡") == []


def test_merge_hits_puts_keyword_first_and_dedupes() -> None:
    keyword, vector = hit(0.0, 3), hit(0.2, 3)

    merged = qa.merge_hits([keyword], [vector, hit(0.3, 4)])

    assert [(h.chunk_index, h.distance) for h in merged] == [(3, 0.0), (4, 0.3)]


def test_keyword_hits_answer_even_when_vector_is_far(
    review_hits: list[SearchHit], monkeypatch: pytest.MonkeyPatch
) -> None:
    review_hits.append(hit(0.9, 0))
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def keyword(db: Any, patterns: list[str], top_k: int, **kwargs: Any) -> list[SearchHit]:
        calls.append((patterns, kwargs))
        return [hit(0.0, 7)]

    monkeypatch.setattr(qa.rag_repository, "search_keyword_chunks", keyword)

    answer = _ask("第四章第一節是什麼？", FakeChat("第一節是一般稅額計算 [1]"))

    assert answer.grounded and answer.sources[0].chunk_index == 7
    assert calls == [
        (["第(4|四)章", "第(1|一)節"], {"knowledge_base_ids": [KB], "department_id": None})
    ]


def test_double_bracket_citation_is_normalized(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))

    answer = _ask("發票遺失怎麼辦？", FakeChat("要取得影本【[1]】"))

    assert answer.grounded and "【" not in answer.text and "[1]" in answer.text


def test_chat_error_note_shows_code_without_content(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.1))
    chat = FakeChat(error=GroqError("groq_invalid_input", "output_parse_failed"))
    chat.understanding = '{"need":"目的","queries":[]}'
    fallback = FakeFallback(error=GeminiError("gemini_quota_exceeded", "RESOURCE_EXHAUSTED"))

    answer = _ask("問題", chat, fallback=fallback)

    assert answer.text == qa.SERVICE_UNAVAILABLE
    assert answer.note == (
        "對話服務錯誤：groq_invalid_input/output_parse_failed；"
        "備援錯誤：gemini_quota_exceeded/RESOURCE_EXHAUSTED"
    )


def test_prompt_and_sources_distinguish_same_article_in_different_knowledge_bases() -> None:
    hits = [hit(0.1), replace(hit(0.1), knowledge_name="另一法規", rag_file_id="f2")]
    chat = FakeChat("兩種法規的第十二條內容 [1][2]。")
    answer = qa.compose_answer("比較第十二條", hits, chat)
    assert answer.grounded
    assert [s.knowledge_name for s in answer.sources] == ["營業稅", "另一法規"]
    prompt = chat.calls[0][0][0].text
    assert "[1] 知識庫「營業稅」《統一發票使用辦法》" in prompt
    assert "[2] 知識庫「另一法規」《統一發票使用辦法》" in prompt


@pytest.mark.parametrize(
    "changes",
    [
        {"approval_status": "pending"},
        {"approval_status": "rejected"},
        {"is_active": False},
        {"file_count": 0},
    ],
)
def test_it_cannot_test_unapproved_inactive_or_empty_kb(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any]
) -> None:
    kb = replace(KnowledgeBase(KB, "測試", "approved", file_count=1), **changes)
    monkeypatch.setattr(qa.rag_repository, "get_knowledge_base", lambda db, key: kb)
    chat = FakeChat("不應呼叫")
    with pytest.raises(RagRuleError) as error:
        _ask("問題", chat)
    assert error.value.code == "KB_NOT_AVAILABLE" and chat.calls == []


def test_answer_prompt_uses_new_analysis_rules_and_budget_without_department(
    review_hits: list[SearchHit],
) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat("取得影本 [1]")
    _ask("發票遺失怎麼辦？", chat)
    messages, kwargs = chat.answer_calls[0]
    system = kwargs["system_instruction"]
    assert "你可以這樣做：" in system and "目前還需要確認：" in system and "依據：" in system
    assert "文件規定：" not in system and "分析：" not in system
    assert "不可把一種情況的處理方法套用到另一種情況" in system
    assert "不可因為某個方法做不到，就自行改用另一種處理方法" in system
    assert "完全無法回答【問題】時，只輸出 NO_ANSWER" in system
    assert "只能說這次查到的資料不足以確認" in system
    assert "能否回答以【問題】和參考段落為準" in system
    assert "即使問題提到第幾項，也不可斷定哪段內容屬於該項" in system
    assert "【問題重點】" not in messages[0].text and "【提問部門】" not in messages[0].text
    assert kwargs["max_tokens"] == 4000 and kwargs["timeout_seconds"] <= 10.0
    assert len(messages) == 1


@pytest.mark.parametrize("ids", [[KB, KB], ["22222222-2222-4222-8222-222222222222", KB, KB]])
def test_department_does_not_change_scope_when_understanding_has_no_queries(
    review_hits: list[SearchHit],
    monkeypatch: pytest.MonkeyPatch,
    ids: list[str],
) -> None:
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def vectors(*args: Any, **kwargs: Any) -> list[SearchHit]:
        calls.append(("vector", args[1:], kwargs))
        return [replace(hit(0.2), knowledge_names=("A", "B"))]

    def keywords(*args: Any, **kwargs: Any) -> list[SearchHit]:
        calls.append(("keyword", args[1:], kwargs))
        return [replace(hit(0.0, 3), knowledge_names=("A", "B"))]

    monkeypatch.setattr(qa.rag_repository, "search_review_chunks", vectors)
    monkeypatch.setattr(qa.rag_repository, "search_keyword_chunks", keywords)
    monkeypatch.setattr(
        qa.rag_repository,
        "get_department",
        lambda db, id: DepartmentOption(id, "finance_d", "財務部"),
    )
    chats = [FakeChat("條文 [1]"), FakeChat("條文 [1]")]
    for dept, chat in zip((None, FINANCE.department_id), chats, strict=True):
        qa.ask_test(
            "第十二條？",
            IT,
            ids,
            dept,
            embedding_factory=FakeEmbedding,
            chat_factory=lambda chat=chat: chat,
            open_session=FakeSession,
        )
    assert calls[:2] == calls[2:]
    assert calls[0][1][-2] == sorted(set(ids))
    assert "【提問部門】財務部" in chats[1].answer_calls[0][0][0].text
    assert "不可編造該部門的內部流程" in chats[1].answer_calls[0][1]["system_instruction"]
    assert "【提問部門】" not in chats[0].answer_calls[0][0][0].text


@pytest.mark.parametrize(
    "ids,dept,error",
    [
        ([], None, "KNOWLEDGE_REQUIRED"),
        ([KB], "unknown", "DEPARTMENT_NOT_AVAILABLE"),
        ([KB], "inactive", "DEPARTMENT_NOT_AVAILABLE"),
    ],
)
def test_test_selection_fails_before_any_embedding(
    review_hits: list[SearchHit],
    monkeypatch: pytest.MonkeyPatch,
    ids: list[str],
    dept: str | None,
    error: str,
) -> None:
    monkeypatch.setattr(qa.rag_repository, "get_department", lambda *args: None)

    def forbidden() -> Any:
        pytest.fail("驗證失敗不應建立向量用戶端")

    with pytest.raises(RagRuleError) as caught:
        qa.ask_test("問題", IT, ids, dept, embedding_factory=forbidden, open_session=FakeSession)
    assert caught.value.code == error


def test_validation_order_and_no_writes(
    review_hits: list[SearchHit],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("測試問答不可寫入或鎖定資料")

    for name, fn in vars(qa.rag_repository).items():
        if inspect.isfunction(fn) and name.startswith(
            (
                "insert_",
                "update_",
                "delete_",
                "set_",
                "upsert_",
                "clear_",
                "queue_",
                "replace_",
                "lock_",
            )
        ):
            monkeypatch.setattr(qa.rag_repository, name, forbidden)
    for name in ("has_qa_access", "list_department_access", "list_knowledge_base_access"):
        monkeypatch.setattr(qa.rag_repository, name, forbidden)
    with pytest.raises(RagRuleError) as caught:
        qa.ask_test(" ", FINANCE, [], open_session=FakeSession)
    assert caught.value.code == "QUESTION_REQUIRED"
    with pytest.raises(RagRuleError) as caught:
        qa.ask_test("問題", FINANCE, [], open_session=FakeSession)
    assert caught.value.code == "NOT_IT_REVIEWER"
    review_hits.append(hit(0.1))
    answer = _ask("問題", FakeChat("內容 [1]"))
    assert answer.grounded


def test_all_selected_kbs_validate_atomically_before_embedding(
    review_hits: list[SearchHit],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def kb(db: Any, id: str) -> KnowledgeBase:
        seen.append(id)
        return KnowledgeBase(id, id, "approved" if id == KB else "pending", file_count=1)

    monkeypatch.setattr(qa.rag_repository, "get_knowledge_base", kb)

    def forbidden() -> Any:
        pytest.fail("其中一個知識庫不可用時不可呼叫向量")

    with pytest.raises(RagRuleError) as caught:
        qa.ask_test(
            "問題", IT, [KB, KB, "second"], embedding_factory=forbidden, open_session=FakeSession
        )
    assert caught.value.code == "KB_NOT_AVAILABLE" and seen == [KB, "second"]


@pytest.mark.parametrize("department", [None, "財務部"])
def test_understanding_uses_default_json_model_and_six_second_budget(
    department: str | None,
) -> None:
    chat = FakeChat()
    chat.understanding = '{"need":"確認遺失後的處理方式","queries":["統一發票遺失處理"]}'
    result = qa.understand_question("發票遺失？", department, chat)
    assert result == qa.QuestionUnderstanding("確認遺失後的處理方式", ("統一發票遺失處理",))
    [messages, kwargs] = chat.calls[0]
    assert messages[0].text == ("【提問部門】財務部\n發票遺失？" if department else "發票遺失？")
    # Groq 的 JSON 模式會以 json_validate_failed 拒絕 20B 的回覆，改由程式解析
    assert kwargs["purpose"] == "default" and "json_output" not in kwargs
    assert kwargs["max_tokens"] == 1600 and kwargs["timeout_seconds"] == 6.0
    assert "保留問題中的法規名稱與條號" in kwargs["system_instruction"]


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"need":"目的","queries":["查詢"]}\n```',
        '以下是結果：{"need":"目的","queries":["查詢"]}',
    ],
)
def test_understanding_reads_json_wrapped_in_extra_text(raw: str) -> None:
    chat = FakeChat()
    chat.understanding = raw
    assert qa.understand_question("問題", None, chat) == qa.QuestionUnderstanding("目的", ("查詢",))


@pytest.mark.parametrize(
    "raw,failure",
    [
        ("not json", "invalid_json"),
        ("null", "invalid_json"),
        ("[]", "invalid_json"),
        ('"text"', "invalid_json"),
        ('{"need":', "invalid_json"),
        ("{}", "invalid_format"),
        ('{"need":1,"queries":[]}', "invalid_format"),
        ('{"need":"目的","queries":"查詢"}', "invalid_format"),
        ('{"need":"目的","queries":["可用",2]}', "invalid_format"),
        ('{"need":"  ","queries":["查詢"]}', "empty_need"),
    ],
)
def test_invalid_understanding_falls_back_and_question_still_answers(
    raw: str, failure: str, review_hits: list[SearchHit], monkeypatch: pytest.MonkeyPatch
) -> None:
    chat = FakeChat("依規定 [1]")
    chat.understanding = raw
    assert qa.understand_question("問題", None, chat) == qa.QuestionUnderstanding(failure=failure)
    embedded = []

    class Embedding(FakeEmbedding):
        def embed_texts(self, texts: Sequence[str], **kwargs: Any) -> EmbeddingBatch:
            embedded.append(list(texts))
            return super().embed_texts(texts, **kwargs)

    review_hits.append(hit(0.1))
    answer = qa.ask_test(
        "原問題",
        IT,
        [KB],
        chat_factory=lambda: chat,
        embedding_factory=Embedding,
        open_session=FakeSession,
    )
    assert answer.grounded and embedded == [["原問題"]]
    assert answer.note == f"問題理解失敗（{failure}），已改用原問題查詢"


@pytest.mark.parametrize(
    "error,reason",
    [
        (GroqError("groq_timeout"), "groq_timeout"),
        (GroqError("groq_failed", "length"), "groq_failed/length"),
    ],
)
def test_understanding_groq_error_is_nonfatal_and_not_logged(
    error: GroqError,
    reason: str,
    review_hits: list[SearchHit],
    caplog: pytest.LogCaptureFixture,
) -> None:
    chat = FakeChat("依規定 [1]")
    chat.understanding_error = error
    review_hits.append(hit(0.1))
    answer = _ask("合成私密問題", chat)
    assert answer.grounded and "合成私密問題" not in caplog.text
    assert [call[1]["purpose"] for call in chat.calls] == ["default", "rag", "rag"]
    assert answer.note == f"問題理解失敗（{reason}），已改用原問題查詢"
    assert "合成私密問題" not in answer.note


def test_understanding_failure_is_added_after_existing_note(
    review_hits: list[SearchHit],
) -> None:
    chat = FakeChat("NO_ANSWER")
    chat.understanding = "not json"
    review_hits.append(hit(0.1))
    answer = _ask("問題", chat)
    assert (
        answer.note == "模型判斷參考段落不足以回答；問題理解失敗（invalid_json），已改用原問題查詢"
    )


def test_successful_understanding_adds_no_note(review_hits: list[SearchHit]) -> None:
    chat = FakeChat("依規定 [1]")
    chat.understanding = '{"need":"目的","queries":["查詢"]}'
    review_hits.append(hit(0.1))
    assert _ask("問題", chat).note is None


def test_understanding_normalizes_deduplicates_and_limits_queries() -> None:
    chat = FakeChat()
    chat.understanding = json.dumps(
        {
            "need": "  目的  ",
            "queries": [
                " ",
                " 原問題 ",
                " 新查詢 ",
                "新查詢",
                "長" * 101,
                "第十二條",
                "界" * 100,
                "超出數量",
            ],
        }
    )
    assert qa.understand_question("原問題", None, chat) == qa.QuestionUnderstanding(
        "目的", ("新查詢", "第十二條", "界" * 100)
    )


def test_queries_share_one_embedding_batch_and_dedupe_by_lowest_distance(
    review_hits: list[SearchHit], monkeypatch: pytest.MonkeyPatch
) -> None:
    events = []
    chat = FakeChat("依規定 [1][2]")
    chat.understanding = '{"need":"處理目的","queries":["查詢一","第九十九條查詢二"]}'

    class Embedding(FakeEmbedding):
        def embed_texts(self, texts: Sequence[str], **kwargs: Any) -> EmbeddingBatch:
            events.append(("embedding", list(texts), kwargs))
            return EmbeddingBatch(tuple((float(i),) * 4 for i in range(len(texts))), "m", 4)

    def search(db: Any, vec: Any, model: str, ids: Any, k: int) -> list[SearchHit]:
        events.append(("search", vec[0], ids, k))
        return {
            0: [hit(0.39, 0), hit(0.3, 1)],
            1: [hit(0.1, 0), hit(0.2, 2)],
            2: [hit(0.15, 0), hit(0.05, 3)],
        }[int(vec[0])]

    keyword_calls = []
    monkeypatch.setattr(qa.rag_repository, "search_review_chunks", search)
    monkeypatch.setattr(
        qa.rag_repository,
        "search_keyword_chunks",
        lambda *args, **kwargs: keyword_calls.append(args) or [],
    )
    answer = qa.ask_test(
        "原問題",
        IT,
        [KB, KB],
        chat_factory=lambda: chat,
        embedding_factory=Embedding,
        open_session=FakeSession,
    )
    assert answer.grounded
    assert events[0] == (
        "embedding",
        ["原問題", "查詢一", "第九十九條查詢二"],
        {"task": "query", "timeout_seconds": qa.retrieval.QUERY_TIMEOUT_SECONDS},
    )
    assert [e[2:] for e in events[1:]] == [([KB], qa.TOP_K)] * 3
    prompt = chat.answer_calls[0][0][0].text
    assert [s.chunk_index for s in answer.sources] == [3, 0]
    assert prompt.count("《統一發票使用辦法》第 1 段") == 1
    assert keyword_calls == []  # 查詢二的第九十九條不可觸發條文比對
    assert "【問題重點】處理目的" in prompt


def test_validation_chat_understanding_then_embedding_order(
    review_hits: list[SearchHit], monkeypatch: pytest.MonkeyPatch
) -> None:
    events = []
    monkeypatch.setattr(
        qa.rag_repository,
        "get_knowledge_base",
        lambda *args: (
            events.append("validate") or KnowledgeBase(KB, "測試", "approved", file_count=1)
        ),
    )
    review_hits.append(hit(0.1))

    class Chat(FakeChat):
        def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatReply:
            events.append(kwargs["purpose"])
            return super().chat(messages, **kwargs)

    class Embedding(FakeEmbedding):
        def embed_texts(self, texts: Sequence[str], **kwargs: Any) -> EmbeddingBatch:
            events.append("embedding")
            return super().embed_texts(texts, **kwargs)

    def factory() -> Chat:
        events.append("client")
        return Chat("回答 [1]")

    qa.ask_test(
        "問題",
        IT,
        [KB],
        embedding_factory=Embedding,
        chat_factory=factory,
        open_session=FakeSession,
    )
    assert events == ["validate", "client", "default", "embedding", "rag", "rag"]


@pytest.mark.parametrize("need", ["", "確認文件適用情形"])
@pytest.mark.parametrize("cause", ["no-hits", "model-no-answer", "bad-citation", "truncated"])
def test_understood_no_answer_only_replaces_missing_evidence_cases(
    need: str, cause: str, review_hits: list[SearchHit]
) -> None:
    chat = FakeChat("NO_ANSWER" if cause == "model-no-answer" else "沒有引用")
    chat.understanding = json.dumps({"need": need, "queries": []})
    if cause != "no-hits":
        review_hits.append(hit(0.1))
    fallback = None
    if cause == "truncated":
        chat.finish_reason = "length"
        fallback = FakeFallback("沒有引用", finish_reason="MAX_TOKENS")
    answer = _ask("問題", chat, fallback=fallback)
    if cause in {"bad-citation", "truncated"}:
        assert answer.text == qa.UNVERIFIED_ANSWER.format(contact=CONTACT)
    elif need:
        assert answer.text == (
            "你想了解的應該是：確認文件適用情形\n"
            "我在「測試知識庫」裡查不到足夠的資料，所以先不回答，以免給你不正確的資訊。\n"
            "可以換個說法，或補充事情的經過再問一次；也可以問負責這項業務的同仁。"
        )
    else:
        assert answer.text == qa.NO_ANSWER
    assert not answer.grounded and answer.sources == () and answer.note


def test_department_changes_understanding_queries_but_never_selected_kb_scope(
    review_hits: list[SearchHit], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        qa.rag_repository,
        "get_department",
        lambda *args: DepartmentOption(FINANCE, "finance_d", "財務部"),
    )
    embedded = []
    scopes = []

    class Embedding(FakeEmbedding):
        def embed_texts(self, texts: Sequence[str], **kwargs: Any) -> EmbeddingBatch:
            embedded.append(list(texts))
            return super().embed_texts(texts, **kwargs)

    monkeypatch.setattr(
        qa.rag_repository,
        "search_review_chunks",
        lambda db, vec, model, ids, k: scopes.append(ids) or [hit(0.1)],
    )
    chats = []
    for department, query in [(None, "一般查詢"), (FINANCE, "財務部查詢")]:
        chat = FakeChat("回答 [1]")
        chat.understanding = json.dumps({"need": "處理目的", "queries": [query]})
        chats.append(chat)
        qa.ask_test(
            "問題",
            IT,
            [KB],
            department,
            embedding_factory=Embedding,
            chat_factory=lambda chat=chat: chat,
            open_session=FakeSession,
        )
    assert embedded == [["問題", "一般查詢"], ["問題", "財務部查詢"]]
    assert scopes == [[KB]] * 4
    assert chats[0].calls[0][0][0].text == "問題"
    assert chats[1].calls[0][0][0].text == "【提問部門】財務部\n問題"
    assert "【提問部門】財務部" in chats[1].answer_calls[0][0][0].text
    assert "不可編造該部門的內部流程" in chats[1].answer_calls[0][1]["system_instruction"]


# ---- 題型、關鍵名詞與白話說明（2026-11-03）----


def test_understanding_reads_type_and_concepts() -> None:
    chat = FakeChat()
    chat.understanding = (
        '{"type":"comparison","concepts":[" 甲 ","乙",3,"丙","丁"],'
        '"need":"差別","queries":["甲","乙"]}'
    )
    result = qa.understand_question("甲乙差在哪", None, chat)
    assert result.question_type == "comparison"
    assert result.concepts == ("甲", "乙", "丙")
    assert "比較題不可只查其中一邊" in chat.calls[0][1]["system_instruction"]


@pytest.mark.parametrize("raw_type", ['"unknown"', "3", "null"])
def test_understanding_ignores_bad_type(raw_type: str) -> None:
    chat = FakeChat()
    chat.understanding = f'{{"type":{raw_type},"need":"目的","queries":[]}}'
    result = qa.understand_question("問題", None, chat)
    assert result.failure is None and result.question_type == "" and result.concepts == ()


def test_system_prompt_allows_plain_explanation_only_for_concepts() -> None:
    assert "只有概念格式的「白話說明」可以用一般常識" in qa.SYSTEM_PROMPT
    assert "白話說明不可寫數字、比例、期限、義務、例外或條號" in qa.SYSTEM_PROMPT
    assert "9. 白話說明" in qa.REVIEW_RULES


@pytest.mark.parametrize(
    "text,expected",
    [
        ("白話說明：\n就是對增值部分課稅。\n主要差別：\n・稅率 5% [1]", False),
        ("白話說明：\n稅率是 5%。\n主要差別：\n・甲 [1]", True),
        ("白話說明：\n依第十條辦理。\n依據：\n・文件 [1]", True),
        ("白話說明：\n稅率是百分之五 [1]。\n主要差別：\n・甲 [1]", False),
        ("適用情況。\n你可以這樣做：\n1、做事 [1]", False),
    ],
)
def test_plain_facts_without_source(text: str, expected: bool) -> None:
    assert qa.plain_facts_without_source(text) is expected


def test_concept_answer_passes_checks(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.1))
    text = (
        "白話說明：\n加值型就是對每一手增加的價值課稅。\n\n"
        "主要差別：\n・甲的計算方式 [1]\n・乙的計算方式 [1]\n\n"
        "跟你的工作有什麼關係：\n・可扣抵 [1]\n\n依據：\n・文件 [1]"
    )
    answer = _ask("甲和乙差在哪？", FakeChat(text))
    assert answer.grounded and answer.text.startswith("白話說明：")


def test_concept_answer_with_uncited_difference_is_discarded(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.1))
    text = "白話說明：\n一句常識。\n\n主要差別：\n・沒有來源的差別\n\n依據：\n・文件 [1]"
    answer = _ask("甲和乙差在哪？", FakeChat(text))
    assert not answer.grounded and "步驟沒有來源" in (answer.note or "")


def test_plain_section_number_without_source_is_discarded(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.1))
    text = "白話說明：\n稅率是 5%。\n\n主要差別：\n・甲 [1]\n\n依據：\n・文件 [1]"
    answer = _ask("甲是什麼？", FakeChat(text))
    assert not answer.grounded and answer.note and "白話說明出現數字或條號" in answer.note


def test_question_type_is_added_to_answer_prompt(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.1))
    chat = FakeChat("重點：\n・甲 [1]")
    chat.understanding = '{"type":"definition","concepts":["甲"],"need":"甲的意思","queries":[]}'
    _ask("甲是什麼？", chat)
    assert "【題型】定義題（問名詞是什麼意思）" in chat.answer_calls[0][0][0].text
