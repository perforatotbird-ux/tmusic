"""
T-Music Playback Models
Resume position, listening history, and per-user favorites.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.utils.time import utcnow


class PlaybackPosition(Base):
    """Last playback offset for a user + VFile (resume)."""
    __tablename__ = "playback_positions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("vfiles.id", ondelete="CASCADE"), index=True)
    position_sec: Mapped[float] = mapped_column(Float, default=0.0)
    duration_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("uq_playback_user_file", "user_id", "file_id", unique=True),
    )


class ListeningHistory(Base):
    """Append-only listening events for 'recent' / stats."""
    __tablename__ = "listening_history"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("vfiles.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    duration_played_sec: Mapped[float] = mapped_column(Float, default=0.0)
    track_duration_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("idx_history_user_started", "user_id", "started_at"),
        Index("idx_history_user_file", "user_id", "file_id"),
    )


class UserFavorite(Base):
    """Per-user favorite track (starred VFile)."""
    __tablename__ = "user_favorites"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_users.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("vfiles.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "file_id", name="uq_user_favorite_file"),
        Index("idx_user_favorites_user", "user_id", "created_at"),
    )
