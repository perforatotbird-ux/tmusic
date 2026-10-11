"""
T-Music Playback Repository

Data-access primitives for resume positions, listening history and favorites.
No transaction management here — the service owns the unit of work.
"""
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.playback import ListeningHistory, PlaybackPosition, UserFavorite
from app.models.vfs import VFile


class PlaybackRepository:
    """Queries + persistence primitives for playback state."""

    @staticmethod
    def get_position(db: Session, user_id: int, file_id: int) -> Optional[PlaybackPosition]:
        return db.scalar(
            select(PlaybackPosition).where(
                PlaybackPosition.user_id == user_id,
                PlaybackPosition.file_id == file_id,
            )
        )

    @staticmethod
    def durations_map(db: Session, file_ids: List[int]) -> dict:
        if not file_ids:
            return {}
        rows = db.execute(
            select(PlaybackPosition.file_id, func.max(PlaybackPosition.duration_sec))
            .where(
                PlaybackPosition.file_id.in_(file_ids),
                PlaybackPosition.duration_sec.isnot(None),
                PlaybackPosition.duration_sec > 0,
            )
            .group_by(PlaybackPosition.file_id)
        ).all()
        return {int(fid): float(dur) for fid, dur in rows if dur}

    @staticmethod
    def list_positions(db: Session, user_id: int, limit: int = 30) -> List[Tuple[PlaybackPosition, Optional[str]]]:
        stmt = (
            select(PlaybackPosition, VFile.name)
            .outerjoin(VFile, VFile.id == PlaybackPosition.file_id)
            .where(PlaybackPosition.user_id == user_id)
            .order_by(PlaybackPosition.updated_at.desc())
            .limit(limit)
        )
        return list(db.execute(stmt).all())

    @staticmethod
    def list_history(
        db: Session, user_id: int, limit: int = 50, offset: int = 0
    ) -> Tuple[List[Tuple[ListeningHistory, Optional[str]]], int]:
        base = select(ListeningHistory).where(ListeningHistory.user_id == user_id)
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        stmt = (
            select(ListeningHistory, VFile.name)
            .outerjoin(VFile, VFile.id == ListeningHistory.file_id)
            .where(ListeningHistory.user_id == user_id)
            .order_by(ListeningHistory.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(db.execute(stmt).all()), total

    @staticmethod
    def get_favorite(db: Session, user_id: int, file_id: int) -> Optional[UserFavorite]:
        return db.scalar(
            select(UserFavorite).where(
                UserFavorite.user_id == user_id,
                UserFavorite.file_id == file_id,
            )
        )

    @staticmethod
    def is_favorite(db: Session, user_id: int, file_id: int) -> bool:
        return db.scalar(
            select(UserFavorite.id).where(
                UserFavorite.user_id == user_id,
                UserFavorite.file_id == file_id,
            )
        ) is not None

    @staticmethod
    def list_favorites(
        db: Session, user_id: int, limit: int = 500, offset: int = 0
    ) -> Tuple[List[Tuple[UserFavorite, Optional[VFile]]], int]:
        filters = (
            UserFavorite.user_id == user_id,
            VFile.is_trash.is_(False),
            VFile.mime_type.like("audio/%"),
        )
        base = select(UserFavorite.id).join(VFile, VFile.id == UserFavorite.file_id).where(*filters)
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        stmt = (
            select(UserFavorite, VFile)
            .outerjoin(VFile, VFile.id == UserFavorite.file_id)
            .where(*filters)
            .order_by(UserFavorite.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(db.execute(stmt).all()), int(total)
