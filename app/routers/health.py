"""
模組名稱：app.routers.health
功能說明：健康檢查端點，供部署驗證與外部監控使用。
路由：GET /health
"""

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """確認網站存活。不連線資料庫，避免資料庫異常時誤判網站本身掛掉。"""
    return {"status": "ok", "env": settings.APP_ENV}
