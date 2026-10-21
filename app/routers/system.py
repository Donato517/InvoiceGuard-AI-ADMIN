"""
模組名稱：app.routers.system
功能說明：系統紀錄——排程執行紀錄、操作稽核紀錄。
路由：GET /system/job-runs、GET /system/audit-logs
注意事項：
    - 排程紀錄頁為「router → service → repository → 樣板」的完整參考寫法，其他頁面依同樣模式實作。
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.deps import DbSession, require_admin
from app.core.templating import templates
from app.services import system_service

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_admin)])


@router.get("/job-runs", response_class=HTMLResponse)
def job_run_list(request: Request, db: DbSession, job_name: str | None = None) -> HTMLResponse:
    """排程執行紀錄（新到舊），可依工作名稱篩選。"""
    job_runs = system_service.list_job_runs(db, job_name=job_name)
    return templates.TemplateResponse(
        request, "pages/system/job_runs.html", {"job_runs": job_runs, "job_name": job_name}
    )


@router.get("/audit-logs", response_class=HTMLResponse)
def audit_log_list(request: Request) -> HTMLResponse:
    """操作稽核紀錄。"""
    # TODO(JerryChien): system_service.list_audit_logs(db, action=..., page=...)
    return templates.TemplateResponse(request, "pages/system/audit_logs.html")
