"""
模組名稱：app.schemas.rag_eval_cases
功能說明：RAG 問答的固定題庫（回歸測試）；每次調整提示詞、切段或檢索後，在 /knowledge/eval 依序執行比對。
注意事項：
    - 2026-11-04 依 RAG_文件 的法規擬題，**待財務審核**；改題目或預期只需修改本檔，不需改程式。
    - 檢查刻意寬鬆：只看是否引用預期文件、回答是否含其中一個關鍵字、格式是否符合題型，不比對全文；
      sources_any／keywords_any 為空代表不檢查該項。
    - 每次執行都會實際呼叫模型並消耗 token。
"""

from dataclasses import dataclass

# 預期格式：概念格式（白話說明…）、步驟格式（你可以這樣做…）；空字串代表不檢查
FORMAT_CONCEPT = "concept"
FORMAT_STEPS = "steps"


@dataclass(frozen=True)
class EvalCase:
    """一題回歸測試。

    Attributes:
        case_id: 固定代碼，畫面與結果比對用。
        question: 問題。
        no_answer: True 代表預期查無資料（範圍外的問題，不可編造回答）。
        sources_any: 引用的文件標題須包含其中一個字串。
        keywords_any: 回答須包含其中一個字串。
        expected_format: FORMAT_CONCEPT、FORMAT_STEPS 或空字串。
    """

    case_id: str
    question: str
    no_answer: bool = False
    sources_any: tuple[str, ...] = ()
    keywords_any: tuple[str, ...] = ()
    expected_format: str = ""


VAT_ACT = "加值型及非加值型營業稅法"

CASES: tuple[EvalCase, ...] = (
    EvalCase(
        "vat-compare",
        "加值型跟非加值型營業稅差在哪？",
        sources_any=(VAT_ACT,),
        keywords_any=("非加值",),
        expected_format=FORMAT_CONCEPT,
    ),
    EvalCase(
        "vat-rate",
        "營業稅的一般稅率是多少？",
        sources_any=(VAT_ACT,),
        keywords_any=("5%", "百分之五", "5％"),
    ),
    EvalCase(
        "vat-filing",
        "營業稅多久申報一次、什麼時候要報？",
        sources_any=(VAT_ACT,),
        keywords_any=("十五日", "15日", "15 日"),
    ),
    EvalCase(
        "zero-rate",
        "什麼是零稅率？哪些銷售適用？",
        sources_any=(VAT_ACT,),
        keywords_any=("外銷",),
        expected_format=FORMAT_CONCEPT,
    ),
    EvalCase(
        "tax-exempt", "哪些銷售可以免徵營業稅？", sources_any=(VAT_ACT,), keywords_any=("免",)
    ),
    EvalCase(
        "invoice-kinds",
        "統一發票有哪幾種？",
        sources_any=("統一發票使用辦法",),
        keywords_any=("三聯式",),
    ),
    EvalCase(
        "triplicate-to",
        "三聯式發票要開給誰？",
        sources_any=("統一發票使用辦法",),
        keywords_any=("營業人",),
    ),
    EvalCase(
        "issue-timing",
        "發票開立的時限怎麼判斷？",
        sources_any=("開立銷售憑證時限表",),
        keywords_any=("開立",),
    ),
    EvalCase(
        "cash-register",
        "使用收銀機開立發票要先辦什麼？",
        sources_any=("收銀機",),
        keywords_any=("申請", "核准"),
    ),
    EvalCase(
        "voucher-retention",
        "會計憑證要保存幾年？",
        sources_any=("商業會計法",),
        keywords_any=("五年", "5年", "5 年"),
    ),
    EvalCase(
        "fiscal-year",
        "會計年度怎麼定？",
        sources_any=("商業會計法",),
        keywords_any=("一月一日", "1月1日"),
    ),
    EvalCase(
        "asset-definition",
        "什麼是資產？",
        sources_any=("商業會計處理準則",),
        keywords_any=("資產",),
        expected_format=FORMAT_CONCEPT,
    ),
    EvalCase(
        "media-file-format",
        "進項資料媒體檔的格式在哪裡規定？",
        sources_any=("作業要點",),
        keywords_any=("附件五",),
    ),
    EvalCase(
        "format-code-21",
        "進項憑證格式代號 21 是什麼？",
        sources_any=("代號",),
        keywords_any=("三聯式",),
    ),
    EvalCase(
        "late-deduction",
        "進項稅額沒有在當期申報扣抵怎麼辦？",
        sources_any=("施行細則",),
        keywords_any=("扣抵",),
    ),
    EvalCase("out-of-scope", "公司尾牙要辦在哪裡？", no_answer=True),
)
