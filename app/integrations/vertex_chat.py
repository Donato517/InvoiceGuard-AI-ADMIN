"""
模組名稱：app.integrations.vertex_chat
功能說明：Google 對話模組——以服務帳號呼叫 Agent Platform（原 Vertex AI）的 generateContent，作為 Groq 的備援。
使用方式：
    from app.integrations.vertex_chat import VertexChatClient
    with VertexChatClient.from_settings() as client:
        reply = client.chat(
            [ChatMessage("user", "問題")], system_instruction="規則", timeout_seconds=6
        )
注意事項：
    - 設定沿用 GOOGLE_CLOUD_PROJECT_ID、GOOGLE_SERVICE_ACCOUNT_JSON、GEMINI_MODEL；不使用 GEMINI_API_KEY，
      Vercel 與 START 不用新增設定。
    - 權杖流程與向量模組相同，回應與錯誤格式與 Gemini 模組相同，所以共用既有函式，錯誤沿用 GeminiError。
    - 只負責呼叫與解析；何時改用備援由上層 service 決定。
"""

import time
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.config import Settings, settings
from app.integrations.chat_models import ChatMessage, ChatReply
from app.integrations.gemini import (
    _ROLES,
    BLOCKED_FINISH_REASONS,
    GeminiCode,
    GeminiError,
    _error_code,
    _google_error,
    _parse_reply,
)
from app.integrations.vertex_embedding import (
    API_BASE,
    LOCATION,
    EmbeddingCode,
    EmbeddingError,
    VertexEmbeddingClient,
)

# 呼叫端未傳 timeout_seconds 時的總預算；低於 ADMIN 函式上限 60 秒
DEFAULT_TIMEOUT_SECONDS = 25.0
_TOKEN_CODES: dict[EmbeddingCode, GeminiCode] = {
    "embedding_not_configured": "gemini_not_configured",
    "embedding_auth_failed": "gemini_auth_failed",
    "embedding_timeout": "gemini_timeout",
    "embedding_unreachable": "gemini_unreachable",
}


def _from_token_error(error: EmbeddingError) -> GeminiError:
    return GeminiError(_TOKEN_CODES.get(error.code, "gemini_failed"), error.detail)


class VertexChatClient:
    """Agent Platform generateContent 用戶端（REST）。自行建立的 HTTP 用戶端由本物件關閉。"""

    def __init__(
        self,
        *,
        project_id: str,
        model: str,
        service_account_json: str,
        http: httpx.Client | None = None,
    ) -> None:
        try:
            self._auth = VertexEmbeddingClient(
                project_id=project_id,
                model=model,
                dimensions=1,
                service_account_json=service_account_json,
                http=http,
            )
        except EmbeddingError as error:
            raise _from_token_error(error) from None
        self._model = model.strip()
        self._url = (
            f"{API_BASE}/projects/{project_id.strip()}/locations/{LOCATION}"
            f"/publishers/google/models/{self._model}:generateContent"
        )

    @classmethod
    def from_settings(cls, configuration: Settings | None = None) -> "VertexChatClient":
        """以專案、服務帳號與 GEMINI_MODEL 建立用戶端。

        Raises:
            GeminiError: gemini_not_configured——專案、模型或服務帳號不合法。
        """
        configuration = configuration if configuration is not None else settings
        return cls(
            project_id=configuration.GOOGLE_CLOUD_PROJECT_ID,
            model=configuration.GEMINI_MODEL,
            service_account_json=configuration.GOOGLE_SERVICE_ACCOUNT_JSON.get_secret_value(),
        )

    @property
    def model(self) -> str:
        """目前使用的模型名稱。"""
        return self._model

    def close(self) -> None:
        """關閉自行建立的 HTTP 用戶端；外部傳入的由呼叫端管理。"""
        self._auth.close()

    def __enter__(self) -> "VertexChatClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        system_instruction: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> ChatReply:
        """送出對話，取得模型回答。

        Args:
            messages: 依時間排序的訊息；最後一則必須是 user。
            system_instruction: 系統指示，不需要時為 None。
            max_tokens: 回答上限；None 時由模型決定。
            timeout_seconds: 含取得權杖的總秒數；None 使用預設（25 秒）。

        Returns:
            ChatReply: 回答文字、模型、結束原因與 token 用量。

        Raises:
            GeminiError: 輸入無效、權限或配額不足、內容被擋下、逾時、無法連線或服務錯誤。
        """
        if (
            not messages
            or any(message.role not in _ROLES or not message.text.strip() for message in messages)
            or _ROLES[messages[-1].role] != "user"
        ):
            raise GeminiError("gemini_invalid_input")
        body: dict[str, Any] = {
            "contents": [
                {"role": _ROLES[message.role], "parts": [{"text": message.text}]}
                for message in messages
            ]
        }
        if system_instruction and system_instruction.strip():
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if max_tokens is not None:
            body["generationConfig"] = {"maxOutputTokens": max_tokens}
        seconds = DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
        if seconds <= 0:
            raise GeminiError("gemini_timeout", "NoTimeLeft")
        deadline = time.monotonic() + seconds
        try:
            token = self._auth._access_token(deadline)
        except EmbeddingError as error:
            raise _from_token_error(error) from None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GeminiError("gemini_timeout", "NoTimeLeft")
        try:
            response = self._auth._http.post(
                self._url,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=httpx.Timeout(remaining),
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise GeminiError("gemini_timeout") from None
        except httpx.HTTPError:
            raise GeminiError("gemini_unreachable") from None
        if time.monotonic() > deadline:
            raise GeminiError("gemini_timeout", "NoTimeLeft")
        if not response.is_success:
            status, reasons = _google_error(response)
            raise GeminiError(
                _error_code(response.status_code, status, reasons), "/".join([status, *reasons])
            )
        try:
            data = response.json()
        except ValueError:
            raise GeminiError("gemini_failed", "InvalidJson") from None
        if not isinstance(data, dict):
            raise GeminiError("gemini_failed", "InvalidJson")
        reply = _parse_reply(data, self._model)
        if reply.finish_reason in BLOCKED_FINISH_REASONS:
            raise GeminiError("gemini_blocked", reply.finish_reason)
        return reply
