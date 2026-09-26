"""
T-Music Playback Service
Resume positions + listening history business logic.
"""
import logging
from typing import List, Optional, Tuple

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.playback import ListeningHistory, PlaybackPosition, UserFavorite
from app.models.vfs import VFile
from app.utils.time import utcnow

logger = logging.getLogger("tmusic.playback")


class PlaybackService:
    """Thin data-access helpers for resume/history/favorites."""

    @staticmethod
    def get_position(db: Session, user_id: int, file_id: int) -> Optional[PlaybackPosition]:
        return db.scalar(
            select(PlaybackPosition).where(
                PlaybackPosition.user_id == user_id,
                PlaybackPosition.file_id == file_id,
            )
        )

    @staticmethod
    def upsert_position(
        db: Session,
        user_id: int,
        file_id: int,
        position_sec: float,
        duration_sec: Optional[float],
    ) -> PlaybackPosition:
        row = PlaybackService.get_position(db, user_id, file_id)
        if row is None:
            row = PlaybackPosition(
                user_id=user_id,
                file_id=file_id,
                position_sec=position_sec,
                duration_sec=duration_sec,
                updated_at=utcnow(),
            )
            db.add(row)
        else:
            row.position_sec = position_sec
            if duration_sec is not None:
                row.duration_sec = duration_sec
            row.updated_at = utcnow()
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def durations_map(db: Session, file_ids: List[int]) -> dict:
        """Best-known duration per file (max over all users' saved positions).

        VFS keeps no probe metadata, so the only trustworthy duration is what
        the player reported back via playback_positions. Returns {} for empty
        input; file ids never played keep an absent key (frontend falls back).
        """
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
        stmt: Select = (
            select(PlaybackPosition, VFile.name)
            .outerjoin(VFile, VFile.id == PlaybackPosition.file_id)
            .where(PlaybackPosition.user_id == user_id)
            .order_by(PlaybackPosition.updated_at.desc())
            .limit(limit)
        )
        return list(db.execute(stmt).all())

    @staticmethod
    def add_history(
        db: Session,
        user_id: int,
        file_id: int,
        duration_played_sec: float,
        track_duration_sec: Optional[float],
        completed: bool,
    ) -> ListeningHistory:
        row = ListeningHistory(
            user_id=user_id,
            file_id=file_id,
            started_at=utcnow(),
            duration_played_sec=duration_played_sec,
            track_duration_sec=track_duration_sec,
            completed=completed,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

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
    def is_favorite(db: Session, user_id: int, file_id: int) -> bool:
        return (
            db.scalar(
                select(UserFavorite.id).where(
                    UserFavorite.user_id == user_id,
                    UserFavorite.file_id == file_id,
                )
            )
            is not None
        )

    @staticmethod
    def toggle_favorite(db: Session, user_id: int, file_id: int) -> bool:
        """Flip star for user+file; returns new is_favorite state."""
        row = db.scalar(
            select(UserFavorite).where(
                UserFavorite.user_id == user_id,
                UserFavorite.file_id == file_id,
            )
        )
        if row:
            db.delete(row)
            db.commit()
            return False
        db.add(UserFavorite(user_id=user_id, file_id=file_id, created_at=utcnow()))
        db.commit()
        return True

    @staticmethod
    def set_favorite(db: Session, user_id: int, file_id: int, value: bool) -> bool:
        """Ensure favorite state is `value`; returns final state."""
        row = db.scalar(
            select(UserFavorite).where(
                UserFavorite.user_id == user_id,
                UserFavorite.file_id == file_id,
            )
        )
        if value and row is None:
            db.add(UserFavorite(user_id=user_id, file_id=file_id, created_at=utcnow()))
            db.commit()
            return True
        if not value and row is not None:
            db.delete(row)
            db.commit()
            return False
        return row is not None

    @staticmethod
    def list_favorites(
        db: Session, user_id: int, limit: int = 500, offset: int = 0
    ) -> Tuple[List[Tuple[UserFavorite, Optional[VFile]]], int]:
        base = select(UserFavorite).where(UserFavorite.user_id == user_id)
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        stmt = (
            select(UserFavorite, VFile)
            .outerjoin(VFile, VFile.id == UserFavorite.file_id)
            .where(
                UserFavorite.user_id == user_id,
                VFile.is_trash == False,  # noqa: E712
                VFile.mime_type.like("audio/%"),
            )
            .order_by(UserFavorite.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(db.execute(stmt).all()), int(total)


playback_service = PlaybackService()
