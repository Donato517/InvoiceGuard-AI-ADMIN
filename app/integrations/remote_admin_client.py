"""
模組名稱：app.integrations.remote_admin_client
功能說明：保留的 ADMIN JSON 傳輸工具（GET／POST／PATCH）；目前沒有呼叫端，不是現行 DB／Storage 串聯路徑。
注意事項：
    - 現行本機 ADMIN 使用 START 注入的設定，透過自己的 DB／Vercel 模組直接連線；不經本檔代理。
    - 只對程式寫定的站內路徑發送請求，不傳送 SQL、DB 密碼、Storage 金鑰或其他 Token。
    - 不跟隨轉址：雲端要求登入時的 303 直接視為未授權，交由呼叫端處理。
    - 雲端回應視為不受信任資料；錯誤一律轉成固定代碼，不含網址、回應原文或例外訊息。
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Literal

TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 65536

Method = Literal["GET", "POST", "PATCH"]
RemoteCode = Literal[
    "remote_not_configured",
    "remote_unreachable",
    "remote_timeout",
    "remote_unauthorized",
    "remote_forbidden",
    "remote_endpoint_missing",
    "remote_server_error",
    "remote_invalid_response",
]


class RemoteAdminError(RuntimeError):
    """呼叫雲端 ADMIN 失敗；只攜帶固定代碼。"""

    def __init__(self, code: RemoteCode) -> None:
        self.code = code
        super().__init__(code)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """3xx 不跟隨，改由 HTTPError 回到呼叫端判斷。"""

    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def request_json(
    method: Method,
    base_url: str,
    path: str,
    *,
    query: dict[str, str] | None = None,
    payload: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    """對雲端 ADMIN 發出 JSON 請求並回傳 HTTP 狀態碼與 JSON 物件。

    Args:
        method: GET、POST 或 PATCH。
        base_url: 核定的雲端 ADMIN HTTPS origin（REMOTE_ADMIN_BASE_URL）。
        path: 程式寫定的站內路徑，必須以 / 開頭；不可來自使用者輸入。
        query: 查詢參數，會經過 URL 編碼。
        payload: POST／PATCH 的 JSON 內容。

    Returns:
        (HTTP 狀態碼, JSON 物件)；4xx／5xx 只要回應是 JSON 物件也會回傳，由呼叫端判斷。

    Raises:
        RemoteAdminError: 未設定網址、連不到、逾時、未授權、禁止、雲端沒有此入口或回應不符。
        ValueError: path 格式不符（程式錯誤，不送出請求）。
    """
    if not base_url:
        raise RemoteAdminError("remote_not_configured")
    if (
        not path.startswith("/")
        or path.startswith("//")
        or any(part in path for part in ("?", "#", ".."))
    ):
        raise ValueError("path 必須是程式寫定的站內路徑")
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    url = base_url.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    status, raw = _send(request)
    if 300 <= status < 400 or status == 401:
        raise RemoteAdminError("remote_unauthorized")
    if status == 403:
        raise RemoteAdminError("remote_forbidden")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        body = None
    if isinstance(body, dict):
        return status, body
    if status == 404:
        raise RemoteAdminError("remote_endpoint_missing")
    if status >= 500:
        raise RemoteAdminError("remote_server_error")
    raise RemoteAdminError("remote_invalid_response")


def get_json(
    base_url: str, path: str, *, query: dict[str, str] | None = None
) -> tuple[int, dict[str, object]]:
    """GET 版本的 request_json。"""
    return request_json("GET", base_url, path, query=query)


def _send(request: urllib.request.Request) -> tuple[int, bytes]:
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as error:
        # 4xx／5xx 與未跟隨的 3xx 都是正常的雲端回應，交由呼叫端依狀態碼判斷
        with error:
            return error.code, error.read(MAX_RESPONSE_BYTES)
    except TimeoutError:
        raise RemoteAdminError("remote_timeout") from None
    except urllib.error.URLError as error:
        code: RemoteCode = (
            "remote_timeout" if isinstance(error.reason, TimeoutError) else "remote_unreachable"
        )
        raise RemoteAdminError(code) from None
    except OSError:
        raise RemoteAdminError("remote_unreachable") from None
