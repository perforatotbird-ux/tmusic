"""
T-Music Telegram-account Repository.
"""
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.vfs import TGAccount


class TGAccountRepository:
    @staticmethod
    def list_all(db: Session) -> List[TGAccount]:
        return list(db.scalars(select(TGAccount)).all())
