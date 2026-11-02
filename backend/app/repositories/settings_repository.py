"""
T-Music Settings Repository — key/value application settings.
"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.auth import AppSetting


class SettingsRepository:
    @staticmethod
    def get(db: Session, key: str) -> Optional[AppSetting]:
        return db.get(AppSetting, key)

    @staticmethod
    def list_all(db: Session) -> List[AppSetting]:
        return list(db.scalars(select(AppSetting)).all())
