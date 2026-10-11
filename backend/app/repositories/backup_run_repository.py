"""
T-Music Backup-Run Repository.
"""
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import BackupRun


class BackupRunRepository:
    @staticmethod
    def list_recent(db: Session, limit: int = 50) -> List[BackupRun]:
        return list(
            db.scalars(select(BackupRun).order_by(BackupRun.started_at.desc()).limit(limit)).all()
        )
