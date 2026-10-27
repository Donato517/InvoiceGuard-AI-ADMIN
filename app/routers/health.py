"""
模組名稱：app.routers.health
功能說明：健康檢查端點，供部署驗證與外部監控使用。
路由：GET /health、GET /health/db（暫時的 DB 連線檢查，僅 Preview／Production）
注意事項：
    - /health/db 只執行唯讀 SELECT 1、不接受任何輸入，回應只有固定欄位與代碼；
      本機 ADMIN 經此取得結果。暫時公開、不需登入，驗證完成後移除。
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.services import db_health_service

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """確認網站存活。不連線資料庫，避免資料庫異常時誤判網站本身掛掉。"""
    return {"status": "ok", "env": settings.APP_ENV}


@router.get("/health/db")
def database_health() -> JSONResponse:
    """唯讀 SELECT 1 連線檢查；DB_OK 回 200，其餘回 503，本機 404。"""
    # SECURITY: 暫時公開、不需登入；只回固定欄位與代碼，驗證完成後移除此路由
    if settings.APP_ENV not in {"preview", "production"}:
        raise HTTPException(status_code=404)
    body = db_health_service.connection_status()
    status_code = 200 if body["status"] == "DB_OK" else 503
    return JSONResponse(body, status_code=status_code, headers={"Cache-Control": "no-store"})
