"""模組名稱：app.services.showcase_intake_service；功能說明：抄錄範例原圖的待核對候選。"""

import json
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.integrations.chat_models import ChatMessage, ChatReply
from app.integrations.document_ai import DocumentAIClient
from app.integrations.gemini import GeminiClient, GeminiError
from app.integrations.groq import GroqClient
from app.integrations.invoice_recognition_contract import SYSTEM_PROMPT, USER_PROMPT
from app.schemas.showcase import MAX_ITEM_LINES

HEADER_FIELDS = (
    "invoice_number",
    "invoice_date",
    "buyer_name",
    "buyer_tax_id",
    "sales_amount",
    "tax_type",
    "tax_amount",
    "total_amount",
    "total_amount_zh",
    "seller_name",
    "seller_tax_id",
    "notes",
)
ITEM_FIELDS = {
    "item_name": "name",
    "quantity": "quantity",
    "unit_price": "unit_price",
    "line_amount": "amount",
}


def _document_ai_groq_fallback(content: bytes, mime: str) -> ChatReply:
    """Gemini Key 未配置時，使用已有的 Document AI 與 Groq 抄錄票面。"""
    deadline = time.monotonic() + 25
    with DocumentAIClient.from_settings() as client:
        ocr = client.process(content, mime, timeout_seconds=15)
    remaining = deadline - time.monotonic()
    if not ocr.text.strip() or remaining <= 0:
        raise ValueError("沒有可用的 OCR 文字")
    return GroqClient.from_settings().chat(
        [ChatMessage("user", USER_PROMPT + "\n\nOCR 文字：\n" + ocr.text[:8000])],
        system_instruction=(
            SYSTEM_PROMPT + "\n本次只提供 Document AI 的 OCR 文字，沒有原圖；"
            "看不清的欄位請填 null，不得依範例推算。"
        ),
        purpose="recognition",
        json_output=True,
        timeout_seconds=remaining,
    )


def extract_candidates(content: bytes, mime: str) -> dict[str, Any]:
    """優先 Gemini 讀圖；缺少 Key 時改用 Document AI OCR＋Groq。"""
    try:
        client = GeminiClient.from_settings()
    except GeminiError as error:
        if error.code != "gemini_not_configured":
            raise
        reply = _document_ai_groq_fallback(content, mime)
        provider = "document_ai+groq"
    else:
        try:
            reply = client.extract_invoice(
                content,
                mime,
                system_instruction=SYSTEM_PROMPT,
                prompt=USER_PROMPT,
                timeout_seconds=25,
            )
        finally:
            client.close()
        provider = "gemini"
    if len(reply.text) > 100_000 or (reply.finish_reason or "").lower() in {"length", "max_tokens"}:
        raise ValueError("辨識結果不完整")
    raw = json.loads(reply.text, parse_float=Decimal)
    if not isinstance(raw, dict) or raw.get("error"):
        raise ValueError("辨識結果格式錯誤")
    candidates = {key: raw.get(key) for key in HEADER_FIELDS}
    items = raw.get("items")
    if isinstance(items, list):
        for index, item in enumerate(items[:MAX_ITEM_LINES], 1):
            if isinstance(item, dict):
                candidates.update(
                    {f"items.{index}.{path}": item.get(key) for key, path in ITEM_FIELDS.items()}
                )
    values = {}
    for key, value in candidates.items():
        if isinstance(value, bool) or isinstance(value, Decimal) and not value.is_finite():
            continue
        if isinstance(value, (str, int, Decimal)) and str(value).strip():
            values[key] = str(value).strip()[:2000]
    if not values:
        raise ValueError("沒有可用的欄位候選")
    return {
        "provider": provider,
        "model": reply.model,
        "created_at": datetime.now(UTC).isoformat(),
        "values": values,
    }
