"""
模組名稱：app.services.rag_qa_service
功能說明：RAG 問答——先理解問題（題型、關鍵名詞、查詢句），再檢索知識庫段落；有相關段落才請回答模型分析，並檢查引用。
相依模組：app.services.rag_retrieval_service、app.db.repositories.rag_repository、
          app.integrations.vertex_embedding、app.integrations.groq
注意事項：
    - 防止幻覺由程式強制，不只靠提示詞：
      1. 沒有段落或距離都超過 MAX_DISTANCE 時不呼叫回答模型，回「找不到」訊息；
      2. 只把找到的段落編號 [1]…[n] 交給模型，要求每個重點標註來源編號；
      3. 回答沒有引用、引用了不存在的編號，或編號步驟沒有來源時，丟棄回答改回固定訊息。
    - 提示詞只放通用規則，不針對個別問題或事件客製；操作內容來自知識庫文件。
    - 依文件回答時的洽詢窗口依目前啟用部門動態判斷（contact_name），新增或改名部門不必改程式。
    - 檢索＝向量相似度＋章節條文文字比對：問題提到「第 X 章／節／條」時另以文字比對補足
      （向量不擅長編號），兩者合併去重後最多 TOP_K 段。
    - 語氣（有溫度）只在固定模板與提示詞的說話方式，不增加事實內容；固定模板不耗 token。
    - 回答先用 Groq；無法回應、額度不足、回覆空白或被截斷時，靜默改用 Vertex，兩者走相同檢查。
    - 回答分兩輪：第一輪寫初稿，第二輪對照段落檢查並修正，連同程式檢查發現的問題一起交給模型；
      沒有完成第二輪或沒有通過檢查的內容一律不顯示。
    - 問題理解、向量與兩輪回答共用同一個截止時間；DB 查詢與驗權不在此預算內。
    - ask（部門問答）與 ask_test（資訊部測試）走同一套流程：先以預設模型理解問題，再多查詢檢索。
      ask 依目前部門指派的知識庫、自動帶入目前部門名稱；ask_test 依勾選的知識庫與選填部門。
      部門影響問題理解與回答角度，不改變知識庫範圍；理解失敗仍以原問題繼續。
    - 題型（2026-11-03）：定義題、比較題用「概念格式」（白話說明 → 主要差別／重點 → 跟你的工作有什麼關係
      → 依據），其他用「步驟格式」。白話說明可以用一般常識解釋名詞意思與制度目的，但不可寫數字、
      期限、義務、例外或條號；程式檢查白話說明有數字或條號卻沒有來源時丟棄回答。
      比較題依每個關鍵名詞各產生一句查詢，避免只查到一邊。
    - 問答為即時對話（v1.5 核定），不保存問題或回答，也不寫入日誌。
    - 查無資料次數（2026-11-05，Schema v2.0 rag_no_answer_stats）：回答為「查無資料」時，檢索範圍內
      每個知識庫當日次數加 1；只記次數，不存問題、回答或使用者。服務失敗、未通過檢查與題庫測試不記錄；
      寫入用獨立短交易，失敗只略過，不影響回答。
    - 版本（2026-11-05）：同名文件（標題去掉括號內年度／版次／修正字樣後相同）有多份且生效日都已知時，
      只用目前有效（生效日 ≤ 今天且最新）的一份；生效日未知的不判斷、照常使用；尚未生效的照常使用，
      段落標頭標示「尚未生效」。
    - 段落多樣化（2026-11-05）：向量檢索的段落同一文件最多 MAX_CHUNKS_PER_FILE 段，其餘文件的段落優先；
      其他文件不足時才補回。章節條文比對的段落不受此限（使用者指定條號）。
    - GroqClient 目前沒有 temperature 參數，無法再降低隨機性；需要時由專案負責人調整共用模組。
"""

import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.repositories import rag_repository
from app.integrations.chat_models import ChatMessage, ChatReply
from app.integrations.gemini import GeminiError
from app.integrations.groq import GroqClient, GroqError
from app.integrations.vertex_chat import VertexChatClient
from app.integrations.vertex_embedding import EmbeddingError, VertexEmbeddingClient
from app.schemas.rag import DepartmentOption, RagActor, RagRuleError, SearchHit
from app.services import rag_retrieval_service as retrieval
from app.utils.format import TAIPEI_TZ

logger = logging.getLogger(__name__)

# 最多給模型幾段（向量檢索與章節條文比對合併後）
TOP_K = 8
# 向量檢索的段落同一文件最多幾段（多樣化；其他文件不足時才補回）
MAX_CHUNKS_PER_FILE = 3
# 章節條文文字比對最多取幾段、最多比對幾組條件
KEYWORD_TOP_K = 4
MAX_REFERENCE_GROUPS = 4
# pgvector 餘弦距離上限（越小越相似）；實測相關句約 0.21、無關句約 0.42，依實際文件再調整
MAX_DISTANCE = 0.40
# 問題長度上限（避免把整份文件貼進來）
MAX_QUESTION_LENGTH = 500
# 理解 6 秒、問題向量 5 秒，其餘由兩輪回答共用；ADMIN 與本機閘道都在 60 秒切斷，保留 DB／傳輸時間。
QA_TOTAL_SECONDS = 56.0
ANSWER_MAX_TOKENS = 4000
# 每輪由 Groq 與備援共用；Groq 逾時後仍要留時間給 Vertex（實測一輪約 10～15 秒）
ROUND_TIMEOUT_SECONDS = 25.0
GROQ_ROUND_TIMEOUT_SECONDS = 10.0
# 剩餘時間不足時不進第二輪
MIN_REVIEW_SECONDS = 8.0
UNDERSTAND_MAX_TOKENS = 1600
UNDERSTAND_TIMEOUT_SECONDS = 6.0
MAX_QUERIES = 3
MAX_QUERY_LENGTH = 100

# 依文件回答時的洽詢窗口：部門名稱含這些字或代碼以此開頭即視為窗口；都沒有時用通稱
# 查無資料時無法判斷問題屬於哪個部門，NO_ANSWER 不指定部門
_CONTACT_NAME_MARKS = ("財務", "會計", "財會")
_CONTACT_CODE_PREFIXES = ("finance", "accounting")
DEFAULT_CONTACT = "負責財會的同仁"

NO_ANSWER = (
    "這個問題我在目前的知識庫裡查不到足夠的資料，所以先不回答，以免給你不正確的資訊。\n"
    "可以換個說法，或補充事情的經過再問一次；也可以問負責這項業務的同仁。"
)
# 檢查沒過不代表文件沒有資料，所以和 NO_ANSWER 分開
UNVERIFIED_ANSWER = (
    "這次整理出的回答不完整或缺少來源，所以先不顯示，以免給你不正確的資訊。\n"
    "請再問一次；也可以直接問{contact}。"
)
SERVICE_UNAVAILABLE = "問答服務暫時無法使用，請稍後再試；若持續發生，請通知資訊部。"
CLOSING = (
    "\n\n以上依知識庫內容整理，實際處理前請以原文為準，不確定時請問{contact}。"
    "還有其他問題隨時問我喔！"
)
_NO_INFO_MARK = "NO_ANSWER"
# Groq 回 length，Vertex 回 MAX_TOKENS
_TRUNCATED_REASONS = frozenset({"length", "MAX_TOKENS"})
_INSUFFICIENT_NOTE = "模型判斷參考段落不足以回答"
# 接受半形 [1]、全形 ［1］ 與 【1】，統一改寫為 [1]
_CITATION = re.compile(r"[\[［【]\s*[\[［]?\s*(\d+)\s*[\]］]?\s*[\]］】]")
# 相鄰的來源視為同一組，例：[1][2]、[1]、[2]
_CITATION_GROUP = re.compile(rf"(?:{_CITATION.pattern}[ \t、,，]*)+")
# 需要來源的區塊：從「你可以這樣做」（步驟格式）或「主要差別／重點」（概念格式）
# 到「目前還需要確認」或「依據」；沒有標題時只看編號步驟
_STEPS_START = re.compile(r"^\s*(?:[你您]可以這樣做|主要差別|重點)\s*[：:]", re.MULTILINE)
_STEPS_END = re.compile(r"^\s*(?:目前還需要確認|依據)\s*[：:]", re.MULTILINE)
# 步驟從行首開始；縮排的編號是上一步的子清單
_STEP_LINE = re.compile(r"^\d{1,2}\s*[、.．)）]")
_BULLET_LINE = re.compile(r"^\s*[・•\-*]")
_TOP_BULLET_LINE = re.compile(r"^[・•\-*]")
# 「目前還需要確認」寫的是缺什麼資料，裡面提到的條號不是引用
_NEEDS_SECTION = re.compile(
    r"^\s*目前還需要確認\s*[：:].*?(?=^\s*依據\s*[：:]|\Z)", re.MULTILINE | re.DOTALL
)
# 概念格式的「白話說明」：到下一個區塊標題為止
_PLAIN_SECTION = re.compile(
    r"^\s*白話說明\s*[：:](.*?)"
    r"(?=^\s*(?:主要差別|重點|跟你的工作有什麼關係|[你您]可以這樣做|目前還需要確認|依據)\s*[：:]|\Z)",
    re.MULTILINE | re.DOTALL,
)
# 白話說明只能解釋名詞與目的；出現數字、比例或條項款就必須有來源
_PLAIN_FACT = re.compile(r"[0-9０-９%％]|百分之|第\s*[〇零一二三四五六七八九十百兩\d]+\s*[條項款]")
# 理解步驟的題型；回答依題型選格式
QUESTION_TYPES = {
    "definition": "定義題（問名詞是什麼意思）",
    "comparison": "比較題（問兩者差在哪）",
    "procedure": "流程題（問該怎麼做）",
}
# Groq／Vertex 錯誤細節只留短代碼，其他內容不進測試說明
_SAFE_DETAIL = re.compile(r"[A-Za-z0-9_/]{1,80}")

SYSTEM_PROMPT = f"""你是 InvoiceGuard 的知識庫小幫手，協助不熟悉規定的同仁，知道眼前這件事該怎麼處理。

回答前先辨認：使用者要處理什麼事、原問題明確提供哪些事實、哪些關鍵條件還不確定。不要把推測當成使用者提供的事實，也不要顯示思考過程。

規則（必須遵守）：
1. 規定、操作要求、例外、期限、數字、比例、金額、條號、文件用途與責任歸屬，只能來自使用者訊息中的【參考段落】，不可使用段落以外的法規知識補充。只有概念格式的「白話說明」可以用一般常識解釋名詞的意思與制度目的，幫助沒接觸過的人先懂概念；白話說明不可寫數字、比例、期限、義務、例外或條號，也不可延伸成規定，不確定的就不寫。
2. 某個處理方法只能用在原文寫明的情況。同一段落可能包含多條規定，不可因為寫在一起就當成都適用；不可把一種情況的處理方法套用到另一種情況；不可因為某個方法做不到，就自行改用另一種處理方法。
3. 每個實質操作都要有支持它的段落，並在該步標註來源編號，例如 [1]、[2]；條件、例外與必要文件也要有依據。來源編號必須是實際寫有該內容的段落：標註前先回到【參考段落】找到那段文字，使用該段開頭的編號；提到第幾條時，標註的段落必須真的含有那一條。只能使用參考段落中出現的編號。
4. 可以把原文寫明的條件與處理方法整理成白話步驟，也可以簡短說明為什麼這樣做；不可增加原文沒有的處理方法、義務、期限或內部流程。每一步寫清楚誰做什麼、向誰取得或交給誰；原文沒有寫明就不要自行判定。
5. 資料只支持一部分時，只回答那一部分並說明適用範圍。缺少決定做法的關鍵條件時，不替使用者選定情況，也不提供沒有依據的處理方法。
6. 【問題重點】只協助理解方向，不是事實或規定的來源；能否回答以【問題】和參考段落為準。
7. 參考段落之外可能還有其他規定，不可說「沒有規定」或「不需要」，只能說這次查到的資料不足以確認。
8. 專有名詞可以改成意思相同的日常用語；涉及文件用途、由誰保管或提供的解釋，仍須有參考段落支持，段落沒有說明時不自行補充。
9. 條、項、款的編號只能照段落原文寫：段落行首的「〔第N條第M項〕」標示該項開始，到下一個標示或下一條之前都屬於該項；段落開頭的「（接續第N條第M項）」表示這段延續該條項；段落沒有標示項次時，即使問題提到第幾項，也不可斷定哪段內容屬於該項，只寫到條，並在「目前還需要確認」說明段落未標示項次。
10. 段落之間的內容不同時，分別列出各自適用的條件，不自行裁決。
11. 不提供法律意見，不替使用者做核准或退回的決定。
12. 參考段落完全無法回答【問題】時，只輸出 {_NO_INFO_MARK}，不要輸出其他文字。

回答格式（畫面只顯示純文字，不要使用 Markdown，不要用 **、#、表格或 | 符號）：
【題型】是定義題或比較題，或問題是在問名詞的意思、兩者差在哪時，用「概念格式」；其他情況用「步驟格式」。

概念格式：
白話說明：
用一到三句一般常識說明名詞的意思或制度目的，像對沒接觸過的人解釋（這一段不標來源編號，不寫數字、比例、期限、義務、例外或條號）。
主要差別：（比較題用這個標題；定義題改用「重點：」）
・每點一句，寫出參考段落中的內容並標註 [來源編號]；比較題要寫出兩邊各是什麼，段落只支持一邊時，說明另一邊這次查到的資料不足
跟你的工作有什麼關係：
・只寫參考段落有依據的影響 [來源編號]；段落沒有寫就省略這一段
依據：
・這次用到的文件名稱與原文標示的條項 [來源編號]
概念格式約 200 至 400 字。

步驟格式：
先用一到兩句說明這個回答適用的情況，直接回應使用者眼前的事。

資料足以支持操作時：
你可以這樣做：
1、誰做什麼，必要時寫向誰取得或交給誰 [來源編號]（每一步寫在同一段，不要用空行分開）
2、下一步，重要條件寫在該步 [來源編號]
情況不同時，用「如果……」列出與問題直接相關且有依據的情況，最多兩種，各自列步驟。

資料不足時：
目前還需要確認：
・缺少什麼事實、它會影響哪個做法、目前哪個做法還不能確定

最後：
依據：
・這次用到的文件名稱與原文標示的條項 [來源編號]

步驟格式正文以三到五步、約 250 至 400 字為目標；不可為了簡短省略條件、執行者或例外。

語氣：用繁體中文，直接、清楚，對正在處理這件事的人說話，不用寫給主管的管理建議口吻。"""

REVIEW_RULES = f"""這是第二輪：【初稿】是上一輪寫的，不要因為初稿寫了就相信它。請逐句對照【參考段落】檢查，再輸出給同仁看的最終回答。

檢查項目：
1. 來源：每個步驟、條件、例外與文件要求，是否真的寫在它標註的段落。內容在別的段落就改成正確編號；提到第幾條時，標註的段落必須真的含有那一條。
2. 適用：處理方法是否用在原文寫明的情況。同一段落有多條規定時，只留下適用於【問題】的部分。
3. 角色：誰做什麼、向誰取得或交給誰，是否和原文一致，有沒有寫反。
4. 增補：原文沒有寫的處理方法、義務、期限或流程要刪除，不可為了完整而補上。
5. 事實：有沒有把使用者沒說的事當成已知事實。
6. 遺漏：原文中會影響做法的條件或例外，有沒有漏掉；有就補上並標來源。
7. 易懂：不懂規定的同仁能不能照著做。
8. 【程式檢查發現的問題】有列出時，一定要修正。
9. 白話說明：只能解釋名詞的意思與制度目的。出現數字、比例、期限、義務、例外或條號時，有段落支持就移到「主要差別」或「重點」並標來源，沒有就刪除；不可把白話說明寫成規定。
10. 格式：定義題、比較題用概念格式，比較題要寫出兩邊各是什麼；流程題用步驟格式。

輸出規則：只輸出修正後的完整回答，沿用上面的回答格式，不要提到初稿、檢查或修改過程。初稿正確就原樣輸出。參考段落完全無法回答【問題】時，只輸出 {_NO_INFO_MARK}。"""

UNDERSTAND_SYSTEM_PROMPT = """你負責在查詢知識庫之前理解問題，只輸出 JSON，格式：
{"type": "definition 或 comparison 或 procedure 或 other", "concepts": ["問題中的關鍵名詞"], "need": "使用者正在處理的事，一句話", "queries": ["用來查詢知識庫的句子"]}

規則：
1. type：問名詞是什麼意思用 definition；問兩個以上的東西差在哪、怎麼區分用 comparison；問該怎麼做、要準備什麼、期限或流程用 procedure；都不是用 other。
2. concepts 列出問題中的關鍵名詞，最多 3 個，照問題原本的說法，不自行增加。
3. need 寫出使用者正在處理的事、問題明說的事實，以及還不確定的關鍵條件；不要把推測寫成事實，也不要寫處理方法或建議。
4. queries 最多 3 句，每句 100 字以內，把日常說法換成規定文件會使用的說法；保留問題中的法規名稱與條號；不要加入問題沒有提到的處理方法。type 是 definition 或 comparison 時，每個關鍵名詞各寫一句查詢（查它的定義、適用對象或計算方式），比較題不可只查其中一邊。
5. 問題看起來只是條文片段時，推測使用者想知道這段文字的出處、意思或適用情形。
6. 有【提問部門】時只當工作背景，不得假設該部門有哪些內部流程。
7. 不回答問題，不引用任何法規內容。"""


@dataclass(frozen=True)
class QuestionUnderstanding:
    """單題理解結果，僅用於當題檢索與回答，不保存或記錄。

    Attributes:
        failure: 理解失敗的固定原因，顯示在測試說明；不含問題或模型回覆內容。
    """

    need: str = ""
    queries: tuple[str, ...] = ()
    failure: str | None = None
    question_type: str = ""
    concepts: tuple[str, ...] = ()


def _json_object(text: str) -> Any:
    """取出回覆中的 JSON 物件；模型可能在前後多加說明或程式碼區塊。

    Raises:
        ValueError: 回覆沒有可解析的 JSON 物件。
    """
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no json object")
    return json.loads(text[start : end + 1])


def _groq_failure(error: GroqError) -> str:
    return f"{error.code}/{error.detail}" if error.detail else error.code


def _remaining(deadline: float) -> float:
    return deadline - time.monotonic()


def understand_question(
    question: str,
    department_name: str | None,
    client: GroqClient,
    deadline: float | None = None,
) -> QuestionUnderstanding:
    """以預設模型理解問題；服務或格式失敗時，退回只查原問題。

    不用 Groq 的 JSON 模式：實測 20B 常被該模式以 json_validate_failed 拒絕，改由程式解析回覆。
    """
    message = f"【提問部門】{department_name}\n{question}" if department_name else question
    try:
        reply = client.chat(
            [ChatMessage("user", message)],
            system_instruction=UNDERSTAND_SYSTEM_PROMPT,
            purpose="default",
            max_tokens=UNDERSTAND_MAX_TOKENS,
            timeout_seconds=(
                UNDERSTAND_TIMEOUT_SECONDS
                if deadline is None
                else min(UNDERSTAND_TIMEOUT_SECONDS, _remaining(deadline))
            ),
        )
    except GroqError as error:
        return QuestionUnderstanding(failure=_groq_failure(error))
    if deadline is not None and _remaining(deadline) <= 0:
        return QuestionUnderstanding(failure="late")
    try:
        data = _json_object(reply.text)
    except ValueError:
        return QuestionUnderstanding(failure="invalid_json")
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("need"), str)
        or not isinstance(data.get("queries"), list)
        or any(not isinstance(query, str) for query in data["queries"])
    ):
        return QuestionUnderstanding(failure="invalid_format")
    if not data["need"].strip():
        return QuestionUnderstanding(failure="empty_need")
    queries = []
    seen = {question.strip()}
    for query in data["queries"]:
        cleaned = query.strip()
        if not cleaned or len(cleaned) > MAX_QUERY_LENGTH or cleaned in seen:
            continue
        seen.add(cleaned)
        queries.append(cleaned)
        if len(queries) == MAX_QUERIES:
            break
    # 題型與關鍵名詞是輔助資訊：格式不符時略過，不影響查詢
    question_type = data.get("type") if data.get("type") in QUESTION_TYPES else ""
    raw_concepts = data.get("concepts")
    concepts = tuple(
        concept.strip()
        for concept in (raw_concepts if isinstance(raw_concepts, list) else [])
        if isinstance(concept, str) and concept.strip()
    )[:MAX_QUERIES]
    return QuestionUnderstanding(
        data["need"].strip(), tuple(queries), question_type=question_type, concepts=concepts
    )


@dataclass(frozen=True)
class QaSource:
    """回答引用的來源。"""

    number: int
    file_title: str
    knowledge_name: str
    chunk_index: int
    effective_date: date | None = None


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
    # 送給模型的段落來源文件（去重、依段落順序）；沒有相關段落時為空
    retrieved_titles: tuple[str, ...] = ()
    # 查無資料（沒有相關段落，或模型判斷段落不足）；服務失敗與未通過檢查為 False
    no_answer: bool = False

    @property
    def failed(self) -> bool:
        """服務失敗或未通過檢查，沒有可用的回答；畫面應保留問題方便重試。"""
        return self.text == SERVICE_UNAVAILABLE or self.text.startswith(
            UNVERIFIED_ANSWER.split("{", 1)[0]
        )


def _relevant(hits: tuple[SearchHit, ...] | list[SearchHit]) -> list[SearchHit]:
    return [hit for hit in hits if hit.distance <= MAX_DISTANCE]


_DIGITS = "零一二三四五六七八九"
# 回答與段落中的條號，接受 第32條、第三十二條、第32-1條、第32條之1
_ARTICLE_MENTION = re.compile(
    r"第\s*([〇零一二三四五六七八九十百兩\d]+)(?:\s*-\s*(\d+))?\s*條"
    r"(?:\s*之\s*([〇零一二三四五六七八九十百兩\d]+))?"
)
_REFERENCE = re.compile(r"第([〇零一二三四五六七八九十百兩\d]+)(條|章|節)")
# 「附件五」「附件 5」與「格式代號 21」「代號21」這類精確編號，向量檢索容易漏掉，另以文字比對補足
_ATTACHMENT = re.compile(r"附件([〇零一二三四五六七八九十百兩\d]+)")
_CODE = re.compile(r"代號(\d{1,3})")


def _to_int(numeral: str) -> int | None:
    """中文或阿拉伯數字轉整數（支援到九百九十九），無法辨識時為 None。"""
    if numeral.isdigit():
        return int(numeral)
    numeral = numeral.replace("〇", "零").replace("兩", "二")
    if not any(char in "十百" for char in numeral):
        # 逐字寫的數字，例：一〇二
        digits = [_DIGITS.find(char) for char in numeral]
        return None if -1 in digits else int("".join(map(str, digits))) or None
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
    """從問題取出「第 X 條／章／節」「附件 X」「代號 NN」，組成文字比對的條件組（組內每個樣式都要符合）。

    樣式同時接受中文與阿拉伯數字，例：第(4|四)章。章與每個節組成一組（「第四章第一節、第二節」
    → [第四章, 第一節]、[第四章, 第二節]）；每個條各自一組。比對對象是去除空白後的段落，
    段落前綴已含章節標題與文件名稱（見 rag_indexing_service.add_heading_context）。附件各自一組；
    代號與「代號」二字同組（文件名稱含「代號」即可，例：發票憑證種類代號清單）。最多 MAX_REFERENCE_GROUPS 組。
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
    compact = re.sub(r"\s+", "", question)
    for numeral in dict.fromkeys(_ATTACHMENT.findall(compact)):
        number = _to_int(numeral)
        if number is not None and number <= 99:
            groups.append([f"附件({number}|{_to_chinese(number)})"])
    for code in dict.fromkeys(_CODE.findall(compact)):
        # 段落已去除空白；代號前後不可緊接其他數字（避免 21 比對到 121、210）
        groups.append(["代號", f"(^|[^0-9]){code}([^0-9]|$)"])
    return groups[:MAX_REFERENCE_GROUPS]


def merge_hits(keyword_hits: list[SearchHit], vector_hits: list[SearchHit]) -> list[SearchHit]:
    """合併章節比對與向量檢索的段落（章節比對優先），去除重複，最多 TOP_K 段。

    向量段落同一文件超過 MAX_CHUNKS_PER_FILE 段的部分排到後面，讓其他文件的段落先進來；
    其他文件不足 TOP_K 時才補回。章節比對的段落不受限（使用者指定條號）。
    """
    merged: dict[tuple[str, int], SearchHit] = {}
    for hit in keyword_hits:
        merged.setdefault((hit.rag_file_id, hit.chunk_index), hit)
    per_file: dict[str, int] = {}
    overflow: list[SearchHit] = []
    for hit in vector_hits:
        key = (hit.rag_file_id, hit.chunk_index)
        if key in merged:
            continue
        if per_file.get(hit.rag_file_id, 0) >= MAX_CHUNKS_PER_FILE:
            overflow.append(hit)
            continue
        per_file[hit.rag_file_id] = per_file.get(hit.rag_file_id, 0) + 1
        merged[key] = hit
    for hit in overflow:
        merged.setdefault((hit.rag_file_id, hit.chunk_index), hit)
    return list(merged.values())[:TOP_K]


# 標題括號內的年度、版次、修正字樣（例：「（113年修正）」「(第2版)」）不影響是否為同一份規定
_VERSION_NOTE = re.compile(r"[（(][^（）()]*(?:\d|年|版|修正|修訂)[^（）()]*[）)]")


def _title_key(title: str) -> str:
    return "".join(_VERSION_NOTE.sub("", title).split()).casefold()


def superseded_file_ids(hits: Sequence[SearchHit], today: date) -> set[str]:
    """同名文件有多份時，找出已被較新版本取代的文件 ID。

    只比較生效日已知的文件：目前有效版本＝生效日 ≤ today 中最新的一份，生效日更早的同名文件視為舊版；
    生效日未知或尚未生效的文件不會被排除。

    Args:
        hits: 檢索到的段落（可含多份文件）。
        today: 今天（台灣日期）。

    Returns:
        應排除的 rag_file_id。
    """
    dates: dict[str, dict[str, date | None]] = {}
    for hit in hits:
        dates.setdefault(_title_key(hit.file_title), {})[hit.rag_file_id] = hit.effective_date
    superseded: set[str] = set()
    for files in dates.values():
        effective = [day for day in files.values() if day is not None and day <= today]
        if len(files) < 2 or not effective:
            continue
        current = max(effective)
        superseded |= {
            file_id for file_id, day in files.items() if day is not None and day < current
        }
    return superseded


def _today() -> date:
    return datetime.now(TAIPEI_TZ).date()


def _prompt(question: str, hits: list[SearchHit], today: date | None = None) -> str:
    current = today or _today()

    def dated(hit: SearchHit) -> str:
        if not hit.effective_date:
            return ""
        pending = "，尚未生效" if hit.effective_date > current else ""
        return f"（生效日 {hit.effective_date.isoformat()}{pending}）"

    blocks = "\n\n".join(
        f"[{n}] 知識庫「{hit.knowledge_name}」《{hit.file_title}》第 {hit.chunk_index + 1} 段"
        + dated(hit)
        + f"\n{hit.content}"
        for n, hit in enumerate(hits, start=1)
    )
    return f"【參考段落】\n{blocks}\n\n【問題】\n{question}"


def check_citations(text: str, count: int) -> list[int] | None:
    """檢查回答的引用：至少一個、且都在 1～count；不合格時回傳 None。"""
    numbers = sorted({int(n) for n in _CITATION.findall(text)})
    if not numbers or any(n < 1 or n > count for n in numbers):
        return None
    return numbers


def uncited_steps(text: str) -> int:
    """計算操作區塊中沒有來源的段落數；頂層步驟或空行各算一段，只有標題的段落不算。

    沒有「你可以這樣做」標題時，第一段視為適用情況的說明，除非它本身就是步驟或清單。
    這裡寧可多擋：寫在區塊內的說明也要有來源，或由第二輪移到其他區塊。
    """
    start = _STEPS_START.search(text)
    body = text[start.end() :] if start else text
    end = _STEPS_END.search(body)
    blocks: list[list[str]] = [[]]
    for line in (body[: end.start()] if end else body).split("\n"):
        if not line.strip() or _STEP_LINE.match(line):
            blocks.append([])
        if line.strip():
            blocks[-1].append(line)
    blocks = [block for block in blocks if block]
    if start is None and blocks and not _lists_action(blocks[0][0]):
        blocks = blocks[1:]
    return sum(
        1
        for block in blocks
        if any(not line.rstrip().endswith(("：", ":")) for line in block)
        and not _CITATION.search("\n".join(block))
    )


def plain_facts_without_source(text: str) -> bool:
    """白話說明出現數字、比例或條項款，卻沒有標註來源時為 True（常識只能解釋名詞與目的）。"""
    return any(
        _PLAIN_FACT.search(section) and not _CITATION.search(section)
        for section in _PLAIN_SECTION.findall(text)
    )


def _lists_action(line: str) -> bool:
    return bool(_STEP_LINE.match(line) or _BULLET_LINE.match(line))


def _article_key(match: re.Match[str]) -> tuple[int, int] | None:
    main, dash, after = match.groups()
    number, sub = _to_int(main), _to_int(dash or after) if dash or after else 0
    return None if number is None or sub is None else (number, sub)


def _article_keys(text: str) -> set[tuple[int, int]]:
    keys = {_article_key(match) for match in _ARTICLE_MENTION.finditer(text)}
    return {key for key in keys if key is not None}


def _citation_scopes(text: str) -> list[str]:
    """依頂層步驟、條列與空行切開；條號只對應同一處的來源，縮排的內容算在上一步。"""
    scopes: list[list[str]] = [[]]
    for line in text.split("\n"):
        if not line.strip() or _STEP_LINE.match(line) or _TOP_BULLET_LINE.match(line):
            scopes.append([])
        scopes[-1].append(line)
    return ["\n".join(scope) for scope in scopes]


def _citing_group(mention: re.Match[str], groups: list[re.Match[str]], text: str) -> re.Match[str]:
    """條號對應的來源：同一行後面最近的一組；沒有時取同一處距離最近的一組。"""
    line_end = text.find("\n", mention.end())
    following = [
        group
        for group in groups
        if group.start() >= mention.end() and (line_end < 0 or group.start() < line_end)
    ]
    if following:
        return following[0]
    return min(
        groups,
        key=lambda group: max(group.start() - mention.end(), mention.start() - group.end()),
    )


def unsupported_articles(text: str, hits: Sequence[SearchHit]) -> list[str]:
    """找出提到的條號不在對應來源段落的情況，只比對結構，不判斷內容是否正確。"""
    available = [_article_keys(hit.content) for hit in hits]
    wrong: set[tuple[int, int]] = set()
    for scope in _citation_scopes(_NEEDS_SECTION.sub("", text)):
        groups = list(_CITATION_GROUP.finditer(scope))
        if not groups:
            continue
        for mention in _ARTICLE_MENTION.finditer(scope):
            key = _article_key(mention)
            cited = _CITATION.findall(_citing_group(mention, groups, scope)[0])
            if key is not None and not any(key in available[int(n) - 1] for n in cited):
                wrong.add(key)
    return [f"第{main}條" + (f"之{sub}" if sub else "") for main, sub in sorted(wrong)]


def contact_name(departments: Sequence[DepartmentOption]) -> str:
    """從啟用中的部門找出洽詢窗口；有多個時都列出，沒有符合時用通稱。"""
    names = [
        department.department_name
        for department in departments
        if any(mark in department.department_name for mark in _CONTACT_NAME_MARKS)
        or department.department_code.lower().startswith(_CONTACT_CODE_PREFIXES)
    ]
    return "或".join(names) or DEFAULT_CONTACT


class _AnswerUnavailableError(Exception):
    """Groq 與備援都無法回應；訊息只含固定代碼，不含問題或回答內容。"""


def _gemini_failure(error: GeminiError) -> str:
    safe = error.detail and _SAFE_DETAIL.fullmatch(error.detail)
    return f"{error.code}/{error.detail}" if safe else error.code


def _groq_client(chat_factory: Callable[[], GroqClient]) -> GroqClient | None:
    try:
        return chat_factory()
    except GroqError:
        return None


def _generate(
    prompt: str,
    system: str,
    client: GroqClient | None,
    fallback_factory: Callable[[], VertexChatClient],
    deadline: float,
) -> tuple[ChatReply, str | None, bool]:
    """先問 Groq；無法回應、額度不足、回覆空白或被截斷時改問 Vertex。

    Args:
        prompt: 使用者訊息。
        system: 系統指示。
        client: Groq 用戶端；None 表示直接用備援。
        fallback_factory: 建立備援用戶端。
        deadline: 整題的截止時間（time.monotonic）。

    Returns:
        (回答, 測試說明, Groq 是否額度不足)；測試說明只在改用備援時有值，使用者看不到。

    Raises:
        _AnswerUnavailableError: 兩個模型都無法回應。
    """
    round_deadline = min(deadline, time.monotonic() + ROUND_TIMEOUT_SECONDS)
    messages = [ChatMessage("user", prompt)]
    cause = "groq_not_used"
    quota_exceeded = False
    if client is not None:
        try:
            reply = client.chat(
                messages,
                system_instruction=system,
                purpose="rag",
                max_tokens=ANSWER_MAX_TOKENS,
                timeout_seconds=min(GROQ_ROUND_TIMEOUT_SECONDS, _remaining(round_deadline)),
            )
            if _remaining(round_deadline) <= 0:
                cause = "groq_late"
            elif reply.finish_reason in _TRUNCATED_REASONS:
                cause = f"groq_truncated/{ANSWER_MAX_TOKENS}"
            else:
                return reply, None, False
        except GroqError as error:
            cause = _groq_failure(error)
            quota_exceeded = error.code == "groq_quota_exceeded"
    try:
        with fallback_factory() as fallback:
            reply = fallback.chat(
                messages,
                system_instruction=system,
                max_tokens=ANSWER_MAX_TOKENS,
                timeout_seconds=min(ROUND_TIMEOUT_SECONDS, _remaining(round_deadline)),
            )
    except GeminiError as error:
        raise _AnswerUnavailableError(
            f"對話服務錯誤：{cause}；備援錯誤：{_gemini_failure(error)}"
        ) from None
    if _remaining(round_deadline) <= 0:
        raise _AnswerUnavailableError(f"對話服務錯誤：{cause}；備援錯誤：逾時")
    return reply, f"已改用備援模型（{cause}）", quota_exceeded


def _check_reply(reply: ChatReply, hits: list[SearchHit], contact: str) -> QaAnswer:
    """檢查模型回覆：截斷、無法回答、引用與步驟來源；Groq 與備援走同一套。"""
    text = _CITATION.sub(lambda m: f"[{m.group(1)}]", (reply.text or "").strip())
    # 〔〕是索引的內部標示，模型照抄時不顯示給同仁
    text = text.replace("〔", "").replace("〕", "")
    tokens = reply.total_tokens
    if reply.finish_reason in _TRUNCATED_REASONS:
        return QaAnswer(
            UNVERIFIED_ANSWER.format(contact=contact),
            False,
            total_tokens=tokens,
            note=f"回答超過 {ANSWER_MAX_TOKENS} tokens 被截斷",
        )
    if _NO_INFO_MARK in text:
        return QaAnswer(
            NO_ANSWER, False, total_tokens=tokens, note=_INSUFFICIENT_NOTE, no_answer=True
        )
    cited = check_citations(text, len(hits))
    if cited is None:
        return QaAnswer(
            UNVERIFIED_ANSWER.format(contact=contact),
            False,
            total_tokens=tokens,
            note="回答沒有正確標註來源，已丟棄",
        )
    missing = uncited_steps(text)
    if missing:
        return QaAnswer(
            UNVERIFIED_ANSWER.format(contact=contact),
            False,
            total_tokens=tokens,
            note=f"回答有 {missing} 個步驟沒有來源，已丟棄",
        )
    if plain_facts_without_source(text):
        return QaAnswer(
            UNVERIFIED_ANSWER.format(contact=contact),
            False,
            total_tokens=tokens,
            note="白話說明出現數字或條號卻沒有來源，已丟棄",
        )
    unsupported = unsupported_articles(text, hits)
    if unsupported:
        return QaAnswer(
            UNVERIFIED_ANSWER.format(contact=contact),
            False,
            total_tokens=tokens,
            note=f"回答提到的{'、'.join(unsupported)}不在標註的段落，已丟棄",
        )
    sources = tuple(
        QaSource(
            n,
            hits[n - 1].file_title,
            hits[n - 1].knowledge_name,
            hits[n - 1].chunk_index,
            hits[n - 1].effective_date,
        )
        for n in cited
    )
    return QaAnswer(
        text + CLOSING.format(contact=contact),
        grounded=True,
        sources=sources,
        total_tokens=tokens,
    )


def _with_notes(answer: QaAnswer, *notes: str | None) -> QaAnswer:
    parts = [note for note in (answer.note, *notes) if note]
    return replace(answer, note="；".join(parts) or None)


def _add_tokens(first: int | None, second: int | None) -> int | None:
    return None if first is None and second is None else (first or 0) + (second or 0)


def compose_answer(
    question: str,
    hits: list[SearchHit],
    client: GroqClient | None,
    department_name: str | None = None,
    need: str | None = None,
    contact: str = DEFAULT_CONTACT,
    fallback_factory: Callable[[], VertexChatClient] = VertexChatClient.from_settings,
    deadline: float | None = None,
    question_type: str = "",
) -> QaAnswer:
    """分兩輪請模型回答：先寫初稿，再對照段落檢查修正；最後由程式檢查引用，不合格時回固定訊息。

    Args:
        question: 問題。
        hits: 已通過距離門檻的段落（依相似度排序）。
        client: 對話用戶端（purpose="rag"）；None 表示直接用備援。
        department_name: 選填提問部門，調整回答角度，不增加文件以外的規定。
        need: 選填問題重點；不提供時不加入該段提示詞。
        contact: 固定訊息的洽詢窗口。
        fallback_factory: 建立 Groq 無法回應時使用的備援用戶端。
        deadline: 整題的截止時間（time.monotonic）；None 時從現在起算 QA_TOTAL_SECONDS。
        question_type: 選填題型（QUESTION_TYPES 的鍵）；決定概念格式或步驟格式。

    Returns:
        問答結果。
    """
    if not hits:
        return QaAnswer(NO_ANSWER, grounded=False, no_answer=True)
    prompt = _prompt(question, hits)
    if question_type in QUESTION_TYPES:
        prompt += f"\n\n【題型】{QUESTION_TYPES[question_type]}"
    if need:
        prompt += f"\n\n【問題重點】{need}"
    system = SYSTEM_PROMPT
    if department_name is not None:
        prompt += f"\n\n【提問部門】{department_name}"
        system += (
            "\n\n部門說明規則：【提問部門】只是工作背景，不代表使用者的職位、權限或買賣方身分。"
            "對正在處理這件事的人說明，不要求該部門制定制度或規範；"
            "不可編造該部門的內部流程，也不可增加參考段落以外的規定。"
        )
    deadline = time.monotonic() + QA_TOTAL_SECONDS if deadline is None else deadline
    try:
        draft, draft_note, quota_exceeded = _generate(
            prompt, system, client, fallback_factory, deadline
        )
    except _AnswerUnavailableError as error:
        return QaAnswer(SERVICE_UNAVAILABLE, grounded=False, note=str(error))
    checked = _check_reply(draft, hits, contact)
    if _NO_INFO_MARK in (draft.text or ""):
        return _with_notes(checked, draft_note)
    if _remaining(deadline) < MIN_REVIEW_SECONDS:
        return _with_notes(
            QaAnswer(SERVICE_UNAVAILABLE, grounded=False), draft_note, "時間不足，未完成第二輪檢查"
        )
    review_prompt = f"{prompt}\n\n【初稿】\n{draft.text}"
    if not checked.grounded:
        review_prompt += f"\n\n【程式檢查發現的問題】\n{checked.note}"
    # 同一題已確定額度不足時，第二輪不再重試受限的模型
    try:
        reviewed, review_note, _ = _generate(
            review_prompt,
            f"{system}\n\n{REVIEW_RULES}",
            None if quota_exceeded else client,
            fallback_factory,
            deadline,
        )
    except _AnswerUnavailableError as error:
        return _with_notes(
            QaAnswer(SERVICE_UNAVAILABLE, grounded=False), draft_note, f"第二輪未完成（{error}）"
        )
    final = _check_reply(reviewed, hits, contact)
    tokens = _add_tokens(draft.total_tokens, reviewed.total_tokens)
    return _with_notes(replace(final, total_tokens=tokens), draft_note, review_note)


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
    *,
    embedding_factory: Callable[[], VertexEmbeddingClient] = VertexEmbeddingClient.from_settings,
    chat_factory: Callable[[], GroqClient] = GroqClient.from_settings,
    open_session: Callable[[], Session] = rag_repository.open_session,
    fallback_factory: Callable[[], VertexChatClient] = VertexChatClient.from_settings,
) -> QaAnswer:
    """部門問答：依目前部門指派的知識庫回答單題，供之後 /knowledge_qa/ 使用；本批沒有路由呼叫。

    與 ask_test 相同流程：自動帶入目前部門名稱，先理解問題（題型、關鍵名詞、查詢句）再多查詢檢索。
    """
    deadline = time.monotonic() + QA_TOTAL_SECONDS
    text = _clean_question(question)
    try:
        db = open_session()
        try:
            department = rag_repository.get_department(db, actor.department_id)
        finally:
            db.close()
    except rag_repository.SQLAlchemyError:
        return QaAnswer(SERVICE_UNAVAILABLE, grounded=False, note="資料庫讀取失敗")
    department_name = department.department_name if department else None
    client, understanding = _understand(text, department_name, chat_factory, deadline)
    answer = _answer(
        text,
        actor,
        None,
        department_name,
        embedding_factory,
        chat_factory,
        open_session,
        understanding=understanding,
        client=client,
        fallback_factory=fallback_factory,
        deadline=deadline,
    )
    if answer.no_answer:
        record_no_answer(open_session, department_id=actor.department_id)
    return _with_understanding_note(answer, understanding)


def _understand(
    text: str,
    department_name: str | None,
    chat_factory: Callable[[], GroqClient],
    deadline: float,
) -> tuple[GroqClient | None, QuestionUnderstanding]:
    """建立對話用戶端並理解問題；用戶端無法建立時回傳 None，理解標記失敗原因。"""
    try:
        client = chat_factory()
    except GroqError as error:
        return None, QuestionUnderstanding(failure=_groq_failure(error))
    return client, understand_question(text, department_name, client, deadline)


def _with_understanding_note(answer: QaAnswer, understanding: QuestionUnderstanding) -> QaAnswer:
    """理解失敗不算錯誤，但測試時要看得出這題少了理解步驟。"""
    if understanding.failure is None:
        return answer
    failure = f"問題理解失敗（{understanding.failure}），已改用原問題查詢"
    return replace(answer, note=f"{answer.note}；{failure}" if answer.note else failure)


def ask_test(
    question: str,
    actor: RagActor,
    knowledge_base_ids: Sequence[str],
    department_id: str | None = None,
    *,
    embedding_factory: Callable[[], VertexEmbeddingClient] = VertexEmbeddingClient.from_settings,
    chat_factory: Callable[[], GroqClient] = GroqClient.from_settings,
    open_session: Callable[[], Session] = rag_repository.open_session,
    fallback_factory: Callable[[], VertexChatClient] = VertexChatClient.from_settings,
    record_no_answer_stats: bool = True,
) -> QaAnswer:
    """資訊部依勾選知識庫，驗證後依序理解、檢索及回答；部門不擴大檢索範圍。

    record_no_answer_stats 為 False 時查無資料不計次（題庫回歸測試使用）。
    """
    deadline = time.monotonic() + QA_TOTAL_SECONDS
    text = _clean_question(question)
    if not actor.is_it:
        raise RagRuleError("NOT_IT_REVIEWER", "只有資訊部可以使用知識庫問答")
    ids = sorted(set(knowledge_base_ids))
    if not ids:
        raise RagRuleError("KNOWLEDGE_REQUIRED", "請至少勾選一個知識庫")
    try:
        db = open_session()
        try:
            knowledge_names = []
            for kb_id in ids:
                kb = rag_repository.get_knowledge_base(db, kb_id)
                if (
                    kb is None
                    or kb.approval_status != "approved"
                    or not kb.is_active
                    or kb.file_count < 1
                ):
                    raise RagRuleError(
                        "KB_NOT_AVAILABLE", "只能測試已核准、啟用且至少有一份檔案的知識庫"
                    )
                knowledge_names.append(kb.knowledge_name)
            department = rag_repository.get_department(db, department_id) if department_id else None
            if department_id and department is None:
                raise RagRuleError("DEPARTMENT_NOT_AVAILABLE", "部門不存在或已停用")
        finally:
            db.close()
    except rag_repository.SQLAlchemyError:
        return QaAnswer(SERVICE_UNAVAILABLE, grounded=False, note="資料庫讀取失敗")
    department_name = department.department_name if department else None
    client, understanding = _understand(text, department_name, chat_factory, deadline)
    answer = _answer(
        text,
        actor,
        ids,
        department_name,
        embedding_factory,
        chat_factory,
        open_session,
        understanding=understanding,
        client=client,
        knowledge_names=knowledge_names,
        fallback_factory=fallback_factory,
        deadline=deadline,
    )
    if answer.no_answer and record_no_answer_stats:
        record_no_answer(open_session, knowledge_base_ids=ids)
    return _with_understanding_note(answer, understanding)


def record_no_answer(
    open_session: Callable[[], Session],
    *,
    knowledge_base_ids: Sequence[str] | None = None,
    department_id: str | None = None,
) -> None:
    """查無資料時，檢索範圍內每個知識庫當日（台灣日期）次數加 1；失敗只略過，不影響回答。

    Args:
        open_session: 開啟 DB Session。
        knowledge_base_ids: 資訊部測試勾選的知識庫；None 時改用 department_id 選用問答的知識庫。
        department_id: 部門問答的目前部門。
    """
    try:
        db = open_session()
        try:
            ids = (
                list(knowledge_base_ids)
                if knowledge_base_ids is not None
                else rag_repository.list_qa_knowledge_base_ids(db, department_id)
                if department_id
                else []
            )
            if ids:
                rag_repository.increment_no_answer(db, ids, datetime.now(TAIPEI_TZ).date())
                db.commit()
        finally:
            db.close()
    except Exception as error:  # 統計只是附帶紀錄，任何失敗都不能影響回答
        # 只記錯誤類別，不記問題、回答或知識庫內容
        logger.warning("rag_no_answer_stats_write_failed error=%s", type(error).__name__)


def _no_answer_text(need: str, knowledge_names: Sequence[str]) -> str:
    if not need:
        return NO_ANSWER
    where = f"「{'、'.join(knowledge_names)}」" if knowledge_names else "目前部門可用的知識庫"
    return (
        f"你想了解的應該是：{need}\n"
        f"我在{where}裡查不到足夠的資料，所以先不回答，以免給你不正確的資訊。\n"
        "可以換個說法，或補充事情的經過再問一次；也可以問負責這項業務的同仁。"
    )


def _load_contact(open_session: Callable[[], Session]) -> str:
    db = open_session()
    try:
        return contact_name(rag_repository.list_departments(db))
    finally:
        db.close()


def _answer(
    text: str,
    actor: RagActor,
    knowledge_base_ids: Sequence[str] | None,
    department_name: str | None,
    embedding_factory: Callable[[], VertexEmbeddingClient],
    chat_factory: Callable[[], GroqClient],
    open_session: Callable[[], Session],
    *,
    understanding: QuestionUnderstanding | None = None,
    client: GroqClient | None = None,
    knowledge_names: Sequence[str] = (),
    fallback_factory: Callable[[], VertexChatClient] = VertexChatClient.from_settings,
    deadline: float | None = None,
) -> QaAnswer:
    """共用的單題檢索、模型與引用檢查；None 表示依目前部門指派，其餘為明確測試清單。"""
    understanding = understanding or QuestionUnderstanding()
    try:
        contact = _load_contact(open_session)
        with embedding_factory() as embedding:
            if knowledge_base_ids is not None:
                batch = embedding.embed_texts(
                    [text, *understanding.queries],
                    task="query",
                    timeout_seconds=(
                        retrieval.QUERY_TIMEOUT_SECONDS
                        if deadline is None
                        else min(retrieval.QUERY_TIMEOUT_SECONDS, _remaining(deadline))
                    ),
                )
                db = open_session()
                try:
                    nearest: dict[tuple[str, int], SearchHit] = {}
                    for vector in batch.vectors:
                        for hit in rag_repository.search_review_chunks(
                            db, vector, batch.model, knowledge_base_ids, TOP_K
                        ):
                            key = (hit.rag_file_id, hit.chunk_index)
                            if key not in nearest or hit.distance < nearest[key].distance:
                                nearest[key] = hit
                    hits = sorted(nearest.values(), key=lambda hit: hit.distance)
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
                        timeout_seconds=(
                            retrieval.QUERY_TIMEOUT_SECONDS
                            if deadline is None
                            else min(retrieval.QUERY_TIMEOUT_SECONDS, _remaining(deadline))
                        ),
                        extra_queries=understanding.queries,
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
                                knowledge_base_ids=knowledge_base_ids,
                                department_id=(
                                    None
                                    if knowledge_base_ids is not None
                                    else UUID(actor.department_id)
                                ),
                            )
                        )
                finally:
                    db.close()
    except EmbeddingError as error:
        return QaAnswer(SERVICE_UNAVAILABLE, grounded=False, note=f"向量服務錯誤：{error.code}")
    except rag_repository.SQLAlchemyError:
        return QaAnswer(SERVICE_UNAVAILABLE, grounded=False, note="資料庫讀取失敗")
    if deadline is not None and _remaining(deadline) <= 0:
        return QaAnswer(SERVICE_UNAVAILABLE, grounded=False, note="超過整題時限")
    nearby = _relevant(hits)
    # 同名文件只用目前有效的版本（舊版段落不送給模型）
    superseded = superseded_file_ids([*keyword_hits, *nearby], _today())
    relevant = merge_hits(
        [hit for hit in keyword_hits if hit.rag_file_id not in superseded],
        [hit for hit in nearby if hit.rag_file_id not in superseded],
    )
    # 送給模型的段落來自哪些文件（題庫測試據此分辨「沒找到」與「找到但答偏」）
    retrieved = tuple(dict.fromkeys(hit.file_title for hit in relevant))
    if not relevant:
        note = (
            f"最相近段落距離 {min(h.distance for h in hits):.2f}，超過門檻 {MAX_DISTANCE}"
            if hits
            else "沒有可檢索的已索引段落"
        )
        return QaAnswer(
            _no_answer_text(understanding.need, knowledge_names),
            grounded=False,
            note=note,
            no_answer=True,
        )
    return replace(
        _compose_with_retrieval(
            text,
            relevant,
            understanding,
            client,
            chat_factory,
            department_name,
            contact,
            fallback_factory,
            deadline,
            knowledge_names,
        ),
        retrieved_titles=retrieved,
    )


def _compose_with_retrieval(
    text: str,
    relevant: list[SearchHit],
    understanding: QuestionUnderstanding,
    client: GroqClient | None,
    chat_factory: Callable[[], GroqClient],
    department_name: str | None,
    contact: str,
    fallback_factory: Callable[[], VertexChatClient],
    deadline: float | None,
    knowledge_names: Sequence[str],
) -> QaAnswer:
    """有相關段落時請模型回答；模型判斷段落不足時改回查無資料訊息。"""
    answer = compose_answer(
        text,
        relevant,
        client if client is not None else _groq_client(chat_factory),
        department_name,
        need=understanding.need,
        contact=contact,
        fallback_factory=fallback_factory,
        deadline=deadline,
        question_type=understanding.question_type,
    )
    if understanding.need and (answer.note or "").startswith(_INSUFFICIENT_NOTE):
        return replace(answer, text=_no_answer_text(understanding.need, knowledge_names))
    return answer
