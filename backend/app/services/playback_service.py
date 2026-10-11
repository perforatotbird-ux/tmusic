"""
T-Music Playback Service

Resume positions + listening history / favorites business logic. All DB access
goes through PlaybackRepository; this layer owns validation + the transaction
boundary.
"""
import logging
import math
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.playback import ListeningHistory, PlaybackPosition, UserFavorite
from app.models.vfs import VFile
from app.repositories.playback_repository import PlaybackRepository
from app.schemas.playback import MAX_HISTORY_DURATION_SEC
from app.utils.time import utcnow

logger = logging.getLogger("tmusic.playback")


class PlaybackService:
    """Thin data-access helpers for resume/history/favorites."""

    @staticmethod
    def get_position(db: Session, user_id: int, file_id: int) -> Optional[PlaybackPosition]:
        return PlaybackRepository.get_position(db, user_id, file_id)

    @staticmethod
    def upsert_position(
        db: Session,
        user_id: int,
        file_id: int,
        position_sec: float,
        duration_sec: Optional[float],
    ) -> PlaybackPosition:
        row = PlaybackRepository.get_position(db, user_id, file_id)
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
        """Best-known duration per file (max over all users' saved positions)."""
        return PlaybackRepository.durations_map(db, file_ids)

    @staticmethod
    def list_positions(db: Session, user_id: int, limit: int = 30) -> List[Tuple[PlaybackPosition, Optional[str]]]:
        return PlaybackRepository.list_positions(db, user_id, limit)

    @staticmethod
    def add_history(
        db: Session,
        user_id: int,
        file_id: int,
        duration_played_sec: float,
        track_duration_sec: Optional[float],
        completed: bool,
    ) -> ListeningHistory:
        for duration in (duration_played_sec, track_duration_sec):
            if duration is not None and (
                not math.isfinite(duration) or not 0 <= duration <= MAX_HISTORY_DURATION_SEC
            ):
                raise ValueError("History duration must be finite and between 0 and 86400 seconds.")
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
        return PlaybackRepository.list_history(db, user_id, limit, offset)

    @staticmethod
    def is_favorite(db: Session, user_id: int, file_id: int) -> bool:
        return PlaybackRepository.is_favorite(db, user_id, file_id)

    @staticmethod
    def toggle_favorite(db: Session, user_id: int, file_id: int) -> bool:
        """Flip star for user+file; returns new is_favorite state."""
        row = PlaybackRepository.get_favorite(db, user_id, file_id)
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
        row = PlaybackRepository.get_favorite(db, user_id, file_id)
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
        return PlaybackRepository.list_favorites(db, user_id, limit, offset)


playback_service = PlaybackService()
