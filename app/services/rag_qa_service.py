"""
模組名稱：app.services.rag_qa_service
功能說明：RAG 問答——檢索知識庫段落，只在找到相關段落時才請模型依段落回答，並檢查引用，杜絕無依據的回答。
相依模組：app.services.rag_retrieval_service、app.db.repositories.rag_repository、
          app.integrations.vertex_embedding、app.integrations.groq
注意事項：
    - 防止幻覺由程式強制，不只靠提示詞：
      1. 沒有段落或距離都超過 MAX_DISTANCE 時不呼叫模型，直接回固定的「找不到」訊息；
      2. 只把找到的段落編號 [1]…[n] 交給模型，要求每個重點標註來源編號；
      3. 回答沒有引用、或引用了不存在的編號時，丟棄回答改回固定訊息。
    - 檢索＝向量相似度＋章節條文文字比對：問題提到「第 X 章／節／條」時另以文字比對補足
      （向量不擅長編號），兩者合併去重後最多 TOP_K 段。
    - 語氣（有溫度）只在固定模板與提示詞的說話方式，不增加事實內容；固定模板不耗 token。
    - 一般部門依目前部門選用的知識庫檢索（五項條件）；資訊部可指定知識庫測試，含未核准但已索引的文件。
    - 問答為即時對話（v1.5 核定），不保存問題或回答，也不寫入日誌。
    - GroqClient 目前沒有 temperature 參數，無法再降低隨機性；需要時由專案負責人調整共用模組。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.repositories import rag_repository
from app.integrations.chat_models import ChatMessage
from app.integrations.groq import GroqClient, GroqError
from app.integrations.vertex_embedding import EmbeddingError, VertexEmbeddingClient
from app.schemas.rag import RagActor, RagRuleError, SearchHit
from app.services import rag_retrieval_service as retrieval

# 最多給模型幾段（向量檢索與章節條文比對合併後）
TOP_K = 8
# 章節條文文字比對最多取幾段、最多比對幾組條件
KEYWORD_TOP_K = 4
MAX_REFERENCE_GROUPS = 4
# pgvector 餘弦距離上限（越小越相似）；實測相關句約 0.21、無關句約 0.42，依實際文件再調整
MAX_DISTANCE = 0.40
# 問題長度上限（避免把整份文件貼進來）
MAX_QUESTION_LENGTH = 500
# 回答 token 上限與時間（ADMIN 函式上限 30 秒，向量約 5 秒）；
# gpt-oss 為推理模型，思考過程也計入上限，太低會在寫出答案前被截斷
ANSWER_MAX_TOKENS = 2000
ANSWER_TIMEOUT_SECONDS = 20.0

NO_ANSWER = (
    "這個問題我在目前的知識庫裡找不到相關規定，所以先不回答，以免給你不正確的資訊。\n"
    "建議換個說法再問一次，或直接洽詢會計部。"
)
SERVICE_UNAVAILABLE = "問答服務暫時無法使用，請稍後再試；若持續發生，請通知資訊部。"
CLOSING = (
    "\n\n以上內容都整理自知識庫的條文，實際判斷仍以原文與會計部說明為準。還有其他問題隨時問我喔！"
)
_NO_INFO_MARK = "NO_ANSWER"
# 接受半形 [1]、全形 ［1］ 與 【1】，統一改寫為 [1]
_CITATION = re.compile(r"[\[［【]\s*[\[［]?\s*(\d{1,2})\s*[\]］]?\s*[\]］】]")

SYSTEM_PROMPT = f"""你是 InvoiceGuard 的知識庫小助理，協助公司同仁查詢財會與發票相關規定。

規則（必須遵守）：
1. 只能根據使用者訊息中【參考段落】的內容回答，不可使用段落以外的知識、推測或常識補充。
2. 每一個重點後面都要標註來源編號，例如 [1]、[2]；只能使用參考段落中出現的編號。
3. 參考段落不足以回答時，只輸出 {_NO_INFO_MARK}，不要輸出其他文字。
4. 不提供法律意見，不替使用者做核准或退回的決定。

語氣：用繁體中文，親切、簡短，像同事間的小幫手。開頭可以用一句話回應問題，接著用條列整理重點，不要寒暄過長。

格式：畫面只顯示純文字，不要使用 Markdown（不要用 **、#、表格或 | 符號）。條列每行以「・」開頭；
使用者要求列表時，每一項寫成一行「項目：說明 [來源編號]」。來源編號一律寫成 [1] 的形式。"""


@dataclass(frozen=True)
class QaSource:
    """回答引用的來源。"""

    number: int
    file_title: str
    knowledge_name: str
    chunk_index: int


@dataclass(frozen=True)
class QaAnswer:
    """一次問答的結果。

    Attributes:
        text: 顯示給使用者的文字。
        grounded: 是否為模型依段落產生且通過引用檢查；固定訊息為 False。
        sources: 實際被引用的來源。
        total_tokens: 模型用量；沒有呼叫模型為 None（不顯示給使用者）。
        note: 給資訊部測試用的原因說明（距離、截斷、未引用）；不含問題或回答內容。
    """

    text: str
    grounded: bool
    sources: tuple[QaSource, ...] = ()
    total_tokens: int | None = None
    note: str | None = None


def _relevant(hits: tuple[SearchHit, ...] | list[SearchHit]) -> list[SearchHit]:
    return [hit for hit in hits if hit.distance <= MAX_DISTANCE]


_DIGITS = "零一二三四五六七八九"
_REFERENCE = re.compile(r"第([〇零一二三四五六七八九十百兩\d]+)(條|章|節)")


def _to_int(numeral: str) -> int | None:
    """中文或阿拉伯數字轉整數（支援到九百九十九），無法辨識時為 None。"""
    if numeral.isdigit():
        return int(numeral)
    numeral = numeral.replace("〇", "零").replace("兩", "二")
    total, current = 0, 0
    for char in numeral:
        if char in _DIGITS:
            current = _DIGITS.index(char)
        elif char in "十百":
            total += (current or 1) * (10 if char == "十" else 100)
            current = 0
        else:
            return None
    return total + current or None


def _to_chinese(number: int) -> str:
    """整數（1～999）轉法規慣用的中文數字，例：10→十、14→十四、101→一百零一。"""
    hundreds, rest = divmod(number, 100)
    tens, ones = divmod(rest, 10)
    text = f"{_DIGITS[hundreds]}百" if hundreds else ""
    if tens:
        text += ("" if tens == 1 and not hundreds else _DIGITS[tens]) + "十"
    elif hundreds and ones:
        text += "零"
    return text + (_DIGITS[ones] if ones else "")


def reference_groups(question: str) -> list[list[str]]:
    """從問題取出「第 X 條／章／節」，組成文字比對的條件組（組內每個樣式都要符合）。

    樣式同時接受中文與阿拉伯數字，例：第(4|四)章。章與每個節組成一組（「第四章第一節、第二節」
    → [第四章, 第一節]、[第四章, 第二節]）；每個條各自一組。比對對象是去除空白後的段落，
    段落前綴已含章節標題（見 rag_indexing_service.add_heading_context）。最多 MAX_REFERENCE_GROUPS 組。
    """
    found: dict[str, list[str]] = {"章": [], "節": [], "條": []}
    for numeral, kind in _REFERENCE.findall(re.sub(r"\s+", "", question)):
        number = _to_int(numeral)
        if number is None or number > 999:
            continue
        pattern = f"第({number}|{_to_chinese(number)}){kind}"
        if pattern not in found[kind]:
            found[kind].append(pattern)
    chapters = found["章"]
    groups = [[*chapters, section] for section in found["節"]]
    if chapters and not groups:
        groups.append(chapters)
    groups.extend([article] for article in found["條"])
    return groups[:MAX_REFERENCE_GROUPS]


def merge_hits(keyword_hits: list[SearchHit], vector_hits: list[SearchHit]) -> list[SearchHit]:
    """合併章節比對與向量檢索的段落（章節比對優先），去除重複，最多 TOP_K 段。"""
    merged: dict[tuple[str, int], SearchHit] = {}
    for hit in (*keyword_hits, *vector_hits):
        merged.setdefault((hit.rag_file_id, hit.chunk_index), hit)
    return list(merged.values())[:TOP_K]


def _prompt(question: str, hits: list[SearchHit]) -> str:
    blocks = "\n\n".join(
        f"[{n}] 《{hit.file_title}》第 {hit.chunk_index + 1} 段\n{hit.content}"
        for n, hit in enumerate(hits, start=1)
    )
    return f"【參考段落】\n{blocks}\n\n【問題】\n{question}"


def check_citations(text: str, count: int) -> list[int] | None:
    """檢查回答的引用：至少一個、且都在 1～count；不合格時回傳 None。"""
    numbers = sorted({int(n) for n in _CITATION.findall(text)})
    if not numbers or any(n < 1 or n > count for n in numbers):
        return None
    return numbers


def compose_answer(question: str, hits: list[SearchHit], client: GroqClient) -> QaAnswer:
    """請模型依段落回答並檢查引用；不合格時回固定訊息。

    Args:
        question: 問題。
        hits: 已通過距離門檻的段落（依相似度排序）。
        client: 對話用戶端（purpose="rag"）。

    Returns:
        問答結果。
    """
    if not hits:
        return QaAnswer(NO_ANSWER, grounded=False)
    try:
        reply = client.chat(
            [ChatMessage("user", _prompt(question, hits))],
            system_instruction=SYSTEM_PROMPT,
            purpose="rag",
            max_tokens=ANSWER_MAX_TOKENS,
            timeout_seconds=ANSWER_TIMEOUT_SECONDS,
        )
    except GroqError as error:
        return QaAnswer(SERVICE_UNAVAILABLE, grounded=False, note=f"對話服務錯誤：{error.code}")
    text = _CITATION.sub(lambda m: f"[{m.group(1)}]", (reply.text or "").strip())
    tokens = reply.total_tokens
    if reply.finish_reason == "length":
        return QaAnswer(
            NO_ANSWER,
            False,
            total_tokens=tokens,
            note=f"回答超過 {ANSWER_MAX_TOKENS} tokens 被截斷",
        )
    if _NO_INFO_MARK in text:
        return QaAnswer(NO_ANSWER, False, total_tokens=tokens, note="模型判斷參考段落不足以回答")
    cited = check_citations(text, len(hits))
    if cited is None:
        return QaAnswer(NO_ANSWER, False, total_tokens=tokens, note="回答沒有正確標註來源，已丟棄")
    sources = tuple(
        QaSource(n, hits[n - 1].file_title, hits[n - 1].knowledge_name, hits[n - 1].chunk_index)
        for n in cited
    )
    return QaAnswer(text + CLOSING, grounded=True, sources=sources, total_tokens=reply.total_tokens)


def _clean_question(question: str) -> str:
    text = (question or "").strip()
    if not text:
        raise RagRuleError("QUESTION_REQUIRED", "請輸入問題")
    if len(text) > MAX_QUESTION_LENGTH:
        raise RagRuleError("QUESTION_TOO_LONG", f"問題最多 {MAX_QUESTION_LENGTH} 字")
    return text


def ask(
    question: str,
    actor: RagActor,
    knowledge_base_id: str | None = None,
    *,
    embedding_factory: Callable[[], VertexEmbeddingClient] = VertexEmbeddingClient.from_settings,
    chat_factory: Callable[[], GroqClient] = GroqClient.from_settings,
    open_session: Callable[[], Session] = rag_repository.open_session,
) -> QaAnswer:
    """回答一個問題。

    一般部門：依目前部門選用的知識庫檢索（檢索五項條件）。
    資訊部指定 knowledge_base_id：在該知識庫已索引的文件中檢索，含未核准文件，供核准前測試。

    Args:
        question: 使用者問題。
        actor: 已驗證的操作者。
        knowledge_base_id: 資訊部測試用的知識庫；一般部門必須為 None。
        embedding_factory: 建立向量用戶端。
        chat_factory: 建立對話用戶端。
        open_session: 開啟 DB 連線（測試替換為替身）。

    Returns:
        問答結果；找不到相關段落或模型回答未通過檢查時為固定訊息。

    Raises:
        RagRuleError: 問題空白或過長、非資訊部指定知識庫。
    """
    text = _clean_question(question)
    if knowledge_base_id and not actor.is_it:
        raise RagRuleError("NOT_IT_REVIEWER", "只有資訊部可以測試未核准的知識庫")
    try:
        with embedding_factory() as embedding:
            if knowledge_base_id:
                batch = embedding.embed_texts(
                    [text], task="query", timeout_seconds=retrieval.QUERY_TIMEOUT_SECONDS
                )
                db = open_session()
                try:
                    hits = rag_repository.search_review_chunks(
                        db, batch.vectors[0], batch.model, knowledge_base_id, TOP_K
                    )
                finally:
                    db.close()
            else:
                hits = list(
                    retrieval.search(
                        text,
                        UUID(actor.user_id),
                        UUID(actor.department_id),
                        TOP_K,
                        embedding,
                        open_session,
                    ).hits
                )
            groups = reference_groups(text)
            keyword_hits: list[SearchHit] = []
            if groups:
                per_group = max(1, KEYWORD_TOP_K // len(groups))
                db = open_session()
                try:
                    for patterns in groups:
                        keyword_hits.extend(
                            rag_repository.search_keyword_chunks(
                                db,
                                patterns,
                                per_group,
                                knowledge_base_id=knowledge_base_id,
                                department_id=(
                                    None if knowledge_base_id else UUID(actor.department_id)
                                ),
                            )
                        )
                finally:
                    db.close()
    except EmbeddingError as error:
        return QaAnswer(SERVICE_UNAVAILABLE, grounded=False, note=f"向量服務錯誤：{error.code}")
    except rag_repository.SQLAlchemyError:
        return QaAnswer(SERVICE_UNAVAILABLE, grounded=False, note="資料庫讀取失敗")
    relevant = merge_hits(keyword_hits, _relevant(hits))
    if not relevant:
        note = (
            f"最相近段落距離 {min(h.distance for h in hits):.2f}，超過門檻 {MAX_DISTANCE}"
            if hits
            else "沒有可檢索的已索引段落"
        )
        return QaAnswer(NO_ANSWER, grounded=False, note=note)
    try:
        return compose_answer(text, relevant, chat_factory())
    except GroqError as error:
        return QaAnswer(SERVICE_UNAVAILABLE, grounded=False, note=f"對話服務錯誤：{error.code}")
