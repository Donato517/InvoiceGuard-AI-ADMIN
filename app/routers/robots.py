"""
模組名稱：app.routers.robots
功能說明：提供 /robots.txt，禁止搜尋引擎索引後台。
路由：GET /robots.txt
注意事項：
    - 另有 <meta name="robots">（base.html）與 X-Robots-Tag 標頭（vercel.json）雙重保護。
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["robots"])

ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt() -> str:
    """回傳 robots.txt 內容。"""
    return ROBOTS_TXT
