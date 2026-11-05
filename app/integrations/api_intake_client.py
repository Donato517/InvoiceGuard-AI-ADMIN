"""模組名稱：app.integrations.api_intake_client
功能說明：ADMIN 後端呼叫既有 InvoiceGuard-AI-API 的發票上傳端點——辨識、必填檢核、業務警示、查重、提交。
注意事項：
    - 只負責 HTTP 與回應解析；不複製 AI 辨識或檢核規則，檢核結果一律來自 API。
    - 授權標頭由 CredentialProvider 提供；認證方案尚未核定，預設的 UnconfiguredCredentials 一律拒絕，
      因此目前無法對任何真實 API 發出請求。測試以 httpx.MockTransport 注入假傳輸。
    - 憑證、請求內容與回應內容不寫入日誌或例外訊息；回應大小與逾時有上限；不跟隨轉址。
    - 提交與辨識若在送出後逾時／中斷，可能已被 API 處理：丟出 ambiguous=True 的 IntakeTransportError，
      由呼叫端查證（以 recognition_id 查案件），不得當成失敗自動重送。
"""

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal, TypeVar
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from app.schemas.intake import (
    CredentialProvider,
    DuplicateView,
    IntakeApiError,
    IntakeNotConfiguredError,
    IntakeTransportError,
    OutboundRequest,
    RecognitionView,
    RequiredCheckView,
    ResubmitView,
    ReturnItemsView,
    SubmitView,
    VoidView,
    WarningCheckView,
)

API_PREFIX = "/api/v1/invoices"
RECOGNIZE_TIMEOUT_SECONDS = 35.0  # API 的 OCR＋模型總預算為 25 秒
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_MESSAGE_CHARS = 200
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# 只讀取、沒有副作用的操作：逾時或回應異常時可以安全重試；其餘（辨識、提交、重送、作廢）結果不明時不得自動重送
READ_ONLY_OPERATIONS = frozenset({"check", "read"})
# API resubmit：首次成功回 201，相同冪等鍵的重試（replayed=true）回 200；兩者都是成功
RESUBMIT_SUCCESS_STATUSES = frozenset({200, 201})

Model = TypeVar("Model", bound=BaseModel)


class UnconfiguredCredentials:
    """認證方案尚未核定：任何請求都拒絕，功能 fail closed。"""

    def headers(self, request: OutboundRequest) -> dict[str, str]:
        raise IntakeNotConfiguredError


class BearerCredentials:
    """以登入時向 API 換發的 Session Token 作為 Authorization: Bearer；Token 來自已驗證使用者自己的登入。"""

    def __init__(self, token: str) -> None:
        self._token = token

    def __repr__(self) -> str:
        return "BearerCredentials(<redacted>)"

    def headers(self, request: OutboundRequest) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}


def login_for_token(
    base_url: str,
    email: str,
    password: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> str | None:
    """用同一組 Email／密碼向 API 登入並取得 Session Token；任何失敗回 None（不洩漏原因，不寫日誌）。

    只在 ADMIN 登入驗證成功後呼叫一次；密碼不保存、不寫日誌。
    """
    try:
        with httpx.Client(
            base_url=_validated_base_url(base_url),
            transport=transport,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            response = client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": password},
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
            return None
        token = response.json().get("session_token")
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    return token if isinstance(token, str) and token else None


def _validated_base_url(base_url: str) -> str:
    parts = urlsplit(base_url.strip())
    secure = parts.scheme == "https" or (
        parts.scheme == "http" and (parts.hostname or "") in LOOPBACK_HOSTS
    )
    if not secure or not parts.hostname or parts.username or parts.password or parts.query:
        raise ValueError("API 位址必須是 https（本機開發才允許 http://127.0.0.1／localhost）")
    return base_url.strip().rstrip("/")


class ApiIntakeClient:
    """對既有發票 API 的薄用戶端；每次呼叫只做一件事，不保存任何狀態。"""

    def __init__(
        self,
        base_url: str,
        credentials: CredentialProvider | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = _validated_base_url(base_url)
        self._credentials: CredentialProvider = credentials or UnconfiguredCredentials()
        self._transport = transport

    # ---- 五個端點 ----

    def recognize(self, content: bytes, filename: str, content_type: str) -> RecognitionView:
        """上傳影像並辨識；API 會保存影像、寫入辨識紀錄（有成本與副作用）。"""
        return self._call(
            "recognize",
            "POST",
            f"{API_PREFIX}/recognize",
            RecognitionView,
            expected=200,
            files={"file": (filename, content, content_type)},
            bind_bytes=content,
            timeout=RECOGNIZE_TIMEOUT_SECONDS,
        )

    def check_required(self, body: Mapping[str, Any]) -> RequiredCheckView:
        return self._call(
            "check", "POST", f"{API_PREFIX}/check-required", RequiredCheckView, 200, json_body=body
        )

    def check_warnings(self, body: Mapping[str, Any]) -> WarningCheckView:
        return self._call(
            "check", "POST", f"{API_PREFIX}/check-warnings", WarningCheckView, 200, json_body=body
        )

    def check_duplicate(self, body: Mapping[str, Any]) -> DuplicateView:
        return self._call(
            "check", "POST", f"{API_PREFIX}/check-duplicate", DuplicateView, 200, json_body=body
        )

    def submit(self, body: Mapping[str, Any]) -> SubmitView:
        """送出發票；失敗重送是否安全由 API 的冪等規則保證（同一辨識只能送出一次）。"""
        return self._call("submit", "POST", API_PREFIX, SubmitView, 201, json_body=body)

    # ---- 退回補正：規則（欄位白名單、輪次、冪等、圖片退件）全部由 API 判斷，這裡只轉送 ----

    def get_return_items(self, invoice_id: str) -> ReturnItemsView:
        """讀取退件項目、後端允許修改的欄位與上一輪提交值（只有原提交者可讀，其餘 API 回 404）。"""
        return self._call(
            "read",
            "GET",
            f"{API_PREFIX}/{_uuid_path(invoice_id)}/return-items",
            ReturnItemsView,
            200,
        )

    def resubmit(self, invoice_id: str, body: Mapping[str, Any]) -> ResubmitView:
        """同案補正／申覆重送；結果不明（逾時）時不得換新冪等鍵重送，應以同一把鍵重試。"""
        return self._call(
            "resubmit",
            "POST",
            f"{API_PREFIX}/{_uuid_path(invoice_id)}/resubmit",
            ResubmitView,
            RESUBMIT_SUCCESS_STATUSES,
            json_body=body,
        )

    def void(self, invoice_id: str, body: Mapping[str, Any]) -> VoidView:
        """申請人作廢被退回的案件（圖片退件的必經步驟）；只轉換狀態，不刪除案件與影像。"""
        return self._call(
            "void",
            "POST",
            f"{API_PREFIX}/{_uuid_path(invoice_id)}/void",
            VoidView,
            200,
            json_body=body,
        )

    # ---- 內部 ----

    def _call(
        self,
        operation: Literal["recognize", "check", "submit", "read", "resubmit", "void"],
        method: str,
        path: str,
        model: type[Model],
        expected: int | frozenset[int],
        *,
        json_body: Mapping[str, Any] | None = None,
        files: Mapping[str, tuple[str, bytes, str]] | None = None,
        bind_bytes: bytes | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> Model:
        payload = (
            json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if json_body is not None
            else None
        )
        bound = payload if payload is not None else (bind_bytes or b"")
        headers = dict(
            self._credentials.headers(
                OutboundRequest(operation, method, path, hashlib.sha256(bound).hexdigest())
            )
        )
        if payload is not None:
            headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        try:
            with httpx.Client(
                base_url=self._base_url,
                transport=self._transport,
                timeout=timeout,
                follow_redirects=False,
            ) as client:
                response = client.request(
                    method, path, content=payload, files=files, headers=headers
                )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise IntakeTransportError(ambiguous=False) from None  # 尚未送出，可安全重試
        except httpx.HTTPError:
            raise IntakeTransportError(ambiguous=operation not in READ_ONLY_OPERATIONS) from None
        return self._parse(response, model, expected, operation)

    @staticmethod
    def _parse(
        response: httpx.Response,
        model: type[Model],
        expected: int | frozenset[int],
        operation: str,
    ) -> Model:
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise IntakeTransportError(
                ambiguous=operation not in READ_ONLY_OPERATIONS, message="發票服務回應異常"
            )
        try:
            body = response.json()
        except ValueError:
            body = None
        accepted = expected if isinstance(expected, frozenset) else frozenset({expected})
        if response.status_code not in accepted:
            raise _api_error(response.status_code, body)
        try:
            return model.model_validate(body)
        except ValidationError:
            # 2xx 但格式不符：API 可能已處理，不能當成失敗
            raise IntakeTransportError(
                ambiguous=operation not in READ_ONLY_OPERATIONS, message="發票服務回應格式異常"
            ) from None


def _uuid_path(invoice_id: str) -> str:
    """路徑只接受標準 UUID，避免把使用者輸入直接拼進 API 路徑。"""
    try:
        return str(UUID(str(invoice_id)))
    except ValueError:
        raise IntakeApiError(404, "NOT_FOUND", "找不到這張發票") from None


def _api_error(status: int, body: object) -> IntakeApiError:
    """解析 API 的標準錯誤格式 {"error": {"code", "message", "details"}}；格式不符用固定代碼。"""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return IntakeApiError(status, "UPSTREAM_ERROR", "發票服務回應異常")
    code = error.get("code")
    message = error.get("message")
    details: list[tuple[tuple[str | int, ...], str]] = []
    raw_details = error.get("details")
    if isinstance(raw_details, list):
        for entry in raw_details:
            if isinstance(entry, dict) and isinstance(entry.get("loc"), list):
                loc = tuple(part for part in entry["loc"] if isinstance(part, str | int))
                details.append((loc, str(entry.get("type", ""))[:64]))
    return IntakeApiError(
        status,
        code[:64] if isinstance(code, str) and code else "UPSTREAM_ERROR",
        message[:MAX_MESSAGE_CHARS] if isinstance(message, str) and message else "發票服務回應異常",
        tuple(details),
    )
