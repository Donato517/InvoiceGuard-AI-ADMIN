"""
模組名稱：app.routers.db_inspection
功能說明：沿用管理者網站登入的最小唯讀 JSON 盤點入口，不接受任意 SQL。
路由：GET /system/db-inspection?table=public.user_profiles
注意事項：未登入／非管理者沿用原有 303／403；不新增金鑰或登入繞過方式。
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.core.deps import require_admin
from app.services import db_inspection_service

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_admin)])
_MESSAGES = {
    "target_not_allowed": "僅能盤點核定資料表",
    "configuration_unavailable": "DB 連線設定尚未完整或格式不符",
    "db_unavailable": "資料庫連線未完成",
    "query_timeout": "唯讀查詢超過允許的等待時間",
    "query_failed": "唯讀查詢未完成，請由受控雲端檢查設定與權限",
}


@router.get("/db-inspection")
def database_inspection(table: str = "public.user_profiles") -> JSONResponse:
    """取得固定範圍的非敏感結構與計數結果；不快取管理者盤點資訊。"""
    headers = {"Cache-Control": "private, no-store", "Pragma": "no-cache"}
    try:
        report = db_inspection_service.inspect_table(table)
    except db_inspection_service.InspectionError as error:
        return JSONResponse(
            {
                "service": "InvoiceGuard-AI-ADMIN",
                "read_only": True,
                "error": {"code": error.code, "message": _MESSAGES[error.code]},
            },
            status_code=400 if error.code == "target_not_allowed" else 503,
            headers=headers,
        )
    return JSONResponse(report.model_dump(by_alias=True), headers=headers)
