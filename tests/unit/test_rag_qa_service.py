"""
模組名稱：tests.unit.test_rag_qa_service
功能說明：RAG 問答防幻覺測試——找不到相關段落不呼叫模型、模型回答沒有引用或引用不存在時改回固定訊息、
          資訊部才能指定知識庫測試、服務錯誤時的固定訊息、問題長度限制。
相依模組：app.services.rag_qa_service
注意事項：
    - 向量、對話與 DB 皆用替身，不呼叫正式 DB、Google 或 Groq。
"""

from collections.abc import Sequence
from typing import Any

import pytest

from app.integrations.chat_models import ChatMessage, ChatReply
from app.integrations.groq import GroqError
from app.integrations.vertex_embedding import EmbeddingBatch, EmbeddingError
from app.schemas.rag import RagActor, RagRuleError, SearchHit
from app.services import rag_qa_service as qa

FINANCE = RagActor("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-00000000000f")
IT = RagActor("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-00000000000e", True)
KB = "11111111-1111-4111-8111-111111111111"


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
        return EmbeddingBatch(vectors=((0.0,) * 4,), model="m", dimensions=4)


class FakeChat:
    def __init__(self, text: str = "", error: GroqError | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[Sequence[ChatMessage], dict[str, Any]]] = []

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatReply:
        self.calls.append((messages, kwargs))
        if self.error:
            raise self.error
        return ChatReply(self.text, "m", "stop", 10, 20, 30, {})


class FakeSession:
    def close(self) -> None:
        return None


@pytest.fixture
def review_hits(monkeypatch: pytest.MonkeyPatch) -> list[SearchHit]:
    hits: list[SearchHit] = []
    monkeypatch.setattr(
        qa.rag_repository, "search_review_chunks", lambda db, vec, model, kb, k: hits
    )
    return hits


def _ask(question: str, chat: FakeChat, actor: RagActor = IT, kb: str | None = KB) -> qa.QaAnswer:
    return qa.ask(
        question,
        actor,
        kb,
        embedding_factory=FakeEmbedding,
        chat_factory=lambda: chat,
        open_session=FakeSession,
    )


def test_no_relevant_passage_does_not_call_model(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.8))
    chat = FakeChat("隨便回答")

    answer = _ask("發票遺失怎麼辦？", chat)

    assert answer.text == qa.NO_ANSWER and not answer.grounded
    assert chat.calls == []


def test_grounded_answer_keeps_cited_sources(review_hits: list[SearchHit]) -> None:
    review_hits.extend([hit(0.2, 0), hit(0.3, 1)])
    chat = FakeChat("可以喔！發票遺失時要取得影本並蓋章 [1]。")

    answer = _ask("發票遺失怎麼辦？", chat)

    assert answer.grounded and answer.text.endswith(qa.CLOSING)
    assert [s.number for s in answer.sources] == [1]
    assert answer.total_tokens == 30
    messages, kwargs = chat.calls[0]
    assert "[1] 《統一發票使用辦法》" in messages[0].text
    assert kwargs["purpose"] == "rag" and kwargs["system_instruction"] == qa.SYSTEM_PROMPT


@pytest.mark.parametrize(
    "reply",
    ["發票遺失要補辦。", "請參考 [3]。", f"{qa._NO_INFO_MARK}"],
    ids=["none", "bad", "mark"],
)
def test_answer_without_valid_citation_is_discarded(
    review_hits: list[SearchHit], reply: str
) -> None:
    review_hits.append(hit(0.2))

    answer = _ask("發票遺失怎麼辦？", FakeChat(reply))

    assert answer.text == qa.NO_ANSWER and not answer.grounded and answer.sources == ()


def test_only_it_can_test_specific_knowledge_base() -> None:
    with pytest.raises(RagRuleError) as error:
        _ask("問題", FakeChat(), actor=FINANCE)

    assert error.value.code == "NOT_IT_REVIEWER"


def test_department_question_uses_five_condition_search(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def search(text: str, user: Any, dept: Any, top_k: int, client: Any, open_session: Any) -> Any:
        seen.append((text, str(dept)))
        return qa.retrieval.SearchResult(hits=(hit(0.1),), sources=())

    monkeypatch.setattr(qa.retrieval, "search", search)

    answer = _ask("發票遺失？", FakeChat("要取得影本 [1]"), actor=FINANCE, kb=None)

    assert answer.grounded and seen == [("發票遺失？", FINANCE.department_id)]


def test_service_errors_return_fixed_message(
    review_hits: list[SearchHit], monkeypatch: pytest.MonkeyPatch
) -> None:
    review_hits.append(hit(0.1))
    assert _ask("問題", FakeChat(error=GroqError("groq_timeout"))).text == qa.SERVICE_UNAVAILABLE

    class BrokenEmbedding(FakeEmbedding):
        def embed_texts(self, texts: Sequence[str], **_: Any) -> EmbeddingBatch:
            raise EmbeddingError("embedding_timeout")

    answer = qa.ask("問題", IT, KB, embedding_factory=BrokenEmbedding, open_session=FakeSession)
    assert answer.text == qa.SERVICE_UNAVAILABLE


@pytest.mark.parametrize("question", ["   ", "長" * (qa.MAX_QUESTION_LENGTH + 1)])
def test_question_must_be_present_and_short(question: str) -> None:
    with pytest.raises(RagRuleError):
        _ask(question, FakeChat())


def test_truncated_reply_is_discarded_with_note(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))
    chat = FakeChat("思考中…")
    chat.chat = lambda messages, **kwargs: ChatReply("", "m", "length", 3000, 2000, 5000, {})  # type: ignore[method-assign]

    answer = _ask("發票遺失怎麼辦？", chat)

    assert answer.text == qa.NO_ANSWER and "截斷" in (answer.note or "")


def test_full_width_citation_is_accepted_and_normalized(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))

    answer = _ask("發票遺失怎麼辦？", FakeChat("要取得影本並蓋章【1】"))

    assert answer.grounded and "[1]" in answer.text


def test_far_passages_note_shows_best_distance(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.55))

    answer = _ask("今天天氣如何？", FakeChat())

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
        (["第(4|四)章", "第(1|一)節"], {"knowledge_base_id": KB, "department_id": None})
    ]


def test_double_bracket_citation_is_normalized(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.2))

    answer = _ask("發票遺失怎麼辦？", FakeChat("要取得影本【[1]】"))

    assert answer.grounded and "【" not in answer.text and "[1]" in answer.text


def test_chat_error_note_shows_code_without_content(review_hits: list[SearchHit]) -> None:
    review_hits.append(hit(0.1))

    answer = _ask("問題", FakeChat(error=GroqError("groq_quota_exceeded")))

    assert answer.note == "對話服務錯誤：groq_quota_exceeded"
