"""
模組名稱：app.services.system_service
功能說明：系統紀錄查詢——排程執行紀錄、操作稽核紀錄。
相依模組：app.db.repositories.job_run_repository、app.db.repositories.audit_log_repository
"""

from sqlalchemy.orm import Session

from app.db.models.job_run import JobRun
from app.db.repositories import job_run_repository

# 排程紀錄頁一次顯示的筆數
JOB_RUN_PAGE_LIMIT = 50


def list_job_runs(db: Session, *, job_name: str | None = None) -> list[JobRun]:
    """取得最近的排程執行紀錄（新到舊）。"""
    return job_run_repository.list_recent(db, job_name=job_name, limit=JOB_RUN_PAGE_LIMIT)


# TODO(JerryChien): list_audit_logs(db, *, action=None, page) -> Page
