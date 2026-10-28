"""
模組名稱：app.core.security
功能說明：登入 cookie 的簽署與驗證、密碼雜湊、登入後導向網址檢查（防 open redirect）、同源檢查（防 CSRF）。
注意事項：
    - 登入 cookie 只存「已簽署的管理者 ID」，以 SESSION_SECRET_KEY 簽署，內容被竄改即失效。
    - 密碼雜湊使用 pwdlib 建議設定（Argon2），必須與 InvoiceGuard-AI-API 使用相同方式，
      兩邊才能驗證同一份 users.password_hash。
    - cookie 設定 SameSite=Lax，並對會變更資料的 POST 請求加上 verify_same_origin 檢查。
"""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Header, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pwdlib import PasswordHash

from app.constants import SESSION_COOKIE_NAME
from app.core.config import settings
from app.core.exceptions import ForbiddenError

_SESSION_SALT = "invoiceguard-admin-session"
_password_hash = PasswordHash.recommended()


# ---- 密碼 ----
def hash_password(password: str) -> str:
    """產生密碼雜湊（建立帳號、重設密碼時使用）。"""
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """比對密碼與雜湊值是否相符。"""
    return _password_hash.verify(password, password_hash)


# ---- 登入 cookie ----
def _serializer() -> URLSafeTimedSerializer:
    """取得 cookie 簽署器。

    Raises:
        RuntimeError: 未設定 SESSION_SECRET_KEY（避免以空金鑰簽署）。
    """
    if settings.is_vercel:
        settings.require_configuration("session")
    secret_key = settings.SESSION_SECRET_KEY.get_secret_value()
    if not secret_key:
        raise RuntimeError("未設定 SESSION_SECRET_KEY")
    return URLSafeTimedSerializer(secret_key, salt=_SESSION_SALT)


def create_session_token(user_id: int) -> str:
    """產生已簽署的登入資料（寫入 cookie 用）。"""
    return _serializer().dumps({"uid": user_id})


def read_session_token(token: str) -> int | None:
    """驗證登入資料並取出管理者 ID。

    Returns:
        管理者 ID；簽章錯誤、遭竄改或超過 SESSION_MAX_AGE_SECONDS 時回傳 None。
    """
    try:
        data = _serializer().loads(token, max_age=settings.SESSION_MAX_AGE_SECONDS)
    except BadSignature:  # 已過期（SignatureExpired）也是 BadSignature 的子類別
        return None
    user_id = data.get("uid") if isinstance(data, dict) else None
    return user_id if isinstance(user_id, int) else None


def set_session_cookie(response: Response, user_id: int) -> None:
    """登入成功後寫入登入 cookie。"""
    response.set_cookie(
        SESSION_COOKIE_NAME,
        create_session_token(user_id),
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    """登出時清除登入 cookie。"""
    response.delete_cookie(
        SESSION_COOKIE_NAME, httponly=True, secure=settings.cookie_secure, samesite="lax"
    )


# ---- 請求檢查 ----
def safe_next_path(next_path: str | None, default: str = "/dashboard") -> str:
    """檢查登入後要導向的網址，只允許站內路徑（防止 open redirect）。

    Returns:
        合法的站內路徑；"//evil.com"、"https://evil.com" 會回傳 default。
    """
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return default
    if "\\" in next_path:
        return default
    return next_path


def verify_same_origin(request: Request, origin: Annotated[str | None, Header()] = None) -> None:
    """確認 POST 等會變更資料的請求來自本站頁面（作為相依注入使用）。

    瀏覽器送出表單時會帶 Origin 標頭；與本站網域不同即拒絕，用來防止 CSRF。
    沒有 Origin 標頭（例如非瀏覽器的工具）時不阻擋，交由 SameSite cookie 保護。

    Raises:
        ForbiddenError: 請求來源與本站不同。
    """
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
        raise ForbiddenError("請求來源不正確")
