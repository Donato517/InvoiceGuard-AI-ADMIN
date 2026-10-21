"""
模組名稱：app.db.repositories.job_run_repository
功能說明：job_runs（排程執行紀錄）的查詢。紀錄由 InvoiceGuard-AI-API 的排程寫入，後台只讀取。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.job_run import JobRun


def list_recent(db: Session, *, job_name: str | None = None, limit: int = 50) -> list[JobRun]:
    """取得最近的排程執行紀錄（新到舊）。

    Args:
        db: 資料庫 Session。
        job_name: 只看指定工作，例如 "sync_einvoice"；None 表示全部。
        limit: 最多筆數。
    """
    stmt = select(JobRun).order_by(JobRun.started_at.desc()).limit(limit)
    if job_name:
        stmt = stmt.where(JobRun.job_name == job_name)
    return list(db.scalars(stmt))
