"""E2E 驅動用的『瀏覽器』：對 ADMIN 服務發真實 HTTP，並把核對頁 HTML 解析成可編輯的表單。"""

import re
from html.parser import HTMLParser
from typing import Any
from uuid import UUID

import httpx

from app.constants import SESSION_COOKIE_NAME

JPEG = (
    b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8
)  # 合成測試影像（符合 JPEG 檔頭即可，內容不是真實發票）


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, list[str]] = {}
        self._select: str | None = None
        self._selected: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "input" and a.get("name") and a.get("type") not in {"file", "submit", "button"}:
            self.fields.setdefault(a["name"], []).append(a.get("value", ""))
        elif tag == "select":
            self._select, self._selected = a.get("name"), ""
        elif tag == "option" and self._select is not None and "selected" in dict(attrs):
            self._selected = a.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._select:
            self.fields.setdefault(self._select, []).append(self._selected or "")
            self._select = None


def parse_form(html: str) -> dict[str, Any]:
    """欄位名 → 值；重複出現的欄位（品名明細的 item_*）為清單，其餘為字串。"""
    parser = _FormParser()
    parser.feed(html)
    return {k: (v if k.startswith("item_") else v[0]) for k, v in parser.fields.items()}


class Browser:
    """以測試帳號登入 ADMIN 的 HTTP 用戶端（手動帶 Cookie，因為本機是 http）。"""

    def __init__(self, stack: Any, name: str) -> None:
        self.stack = stack
        self.user = stack.users[name]
        self.client = httpx.Client(base_url=stack.admin_url, follow_redirects=False, timeout=60)
        self.origin = {"Origin": stack.admin_url}

    def login(self) -> httpx.Response:
        response = self.client.post(
            "/login",
            headers=self.origin,
            data={"email": self.user["email"], "password": self.user["password"]},
        )
        assert response.status_code == 303, response.text[:500]
        cookie = response.cookies.get(SESSION_COOKIE_NAME) or _cookie_from_header(response)
        self.client.cookies.set(SESSION_COOKIE_NAME, cookie)
        return response

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.client.get(path, **kwargs)

    def recognize(
        self, content: bytes = JPEG, filename: str = "invoice.jpg", content_type: str = "image/jpeg"
    ) -> httpx.Response:
        return self.client.post(
            "/intake/recognize",
            headers=self.origin,
            files={"file": (filename, content, content_type)},
        )

    def review(self, action: str, form: dict[str, Any]) -> httpx.Response:
        return self.client.post(
            "/intake/review", headers=self.origin, data={**form, "action": action}
        )

    def close(self) -> None:
        self.client.close()


def _cookie_from_header(response: httpx.Response) -> str:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{SESSION_COOKIE_NAME}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError("登入回應沒有 Session Cookie")


def uuid_of(text_value: str) -> UUID:
    match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text_value)
    assert match, "找不到 UUID"
    return UUID(match.group(0))
