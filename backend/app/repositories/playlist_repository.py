"""
T-Music Playlist Repository

Data-access primitives for playlists and their items. No transaction management
here — the service owns the unit of work (add/flush/commit/rollback).
"""
from typing import List, Optional, Tuple

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import AppUser
from app.models.playlist import Playlist, PlaylistItem
from app.models.vfs import VFile


class PlaylistRepository:
    """Queries + persistence primitives for playlists."""

    @staticmethod
    def list_with_counts(db: Session, user_id: int) -> List[Tuple[Playlist, int]]:
        track_count = (
            select(PlaylistItem.playlist_id, func.count(PlaylistItem.id).label("cnt"))
            .group_by(PlaylistItem.playlist_id)
            .subquery()
        )
        stmt = (
            select(Playlist, func.coalesce(track_count.c.cnt, 0))
            .outerjoin(track_count, track_count.c.playlist_id == Playlist.id)
            .where(Playlist.user_id == user_id)
            .order_by(Playlist.updated_at.desc())
        )
        return list(db.execute(stmt).all())

    @staticmethod
    def get(db: Session, user_id: int, playlist_id: int, *, for_update: bool = False) -> Optional[Playlist]:
        stmt = select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user_id)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        else:
            stmt = stmt.options(selectinload(Playlist.items))
        return db.scalar(stmt)

    @staticmethod
    def lock_user(db: Session, user_id: int) -> Optional[AppUser]:
        return db.scalar(select(AppUser).where(AppUser.id == user_id).with_for_update())

    @staticmethod
    def list_by_user(db: Session, user_id: int) -> List[Playlist]:
        return list(
            db.scalars(select(Playlist).where(Playlist.user_id == user_id).order_by(Playlist.id)).all()
        )

    @staticmethod
    def valid_file_ids(db: Session, file_ids: List[int]) -> set:
        return set(
            db.scalars(
                select(VFile.id).where(VFile.id.in_(file_ids), VFile.is_trash == False)  # noqa: E712
            ).all()
        )

    @staticmethod
    def get_vfile(db: Session, file_id: int) -> Optional[VFile]:
        return db.scalar(select(VFile).where(VFile.id == file_id, VFile.is_trash.is_(False)))

    @staticmethod
    def delete_items(db: Session, playlist_id: int) -> None:
        db.execute(delete(PlaylistItem).where(PlaylistItem.playlist_id == playlist_id))

    @staticmethod
    def get_item(db: Session, playlist_id: int, file_id: int) -> Optional[PlaylistItem]:
        return db.scalar(
            select(PlaylistItem).where(
                PlaylistItem.playlist_id == playlist_id,
                PlaylistItem.file_id == file_id,
            )
        )

    @staticmethod
    def get_item_by_id(db: Session, playlist_id: int, item_id: int) -> Optional[PlaylistItem]:
        return db.scalar(
            select(PlaylistItem).where(
                PlaylistItem.id == item_id,
                PlaylistItem.playlist_id == playlist_id,
            )
        )

    @staticmethod
    def max_position(db: Session, playlist_id: int) -> int:
        return db.scalar(
            select(func.coalesce(func.max(PlaylistItem.position), 0)).where(
                PlaylistItem.playlist_id == playlist_id
            )
        ) or 0

    @staticmethod
    def list_items(db: Session, playlist_id: int) -> List[PlaylistItem]:
        return list(db.scalars(select(PlaylistItem).where(PlaylistItem.playlist_id == playlist_id)).all())

    @staticmethod
    def tracks(db: Session, playlist_id: int) -> List[Tuple[PlaylistItem, Optional[VFile]]]:
        stmt = (
            select(PlaylistItem, VFile)
            .outerjoin(VFile, VFile.id == PlaylistItem.file_id)
            .where(PlaylistItem.playlist_id == playlist_id)
            .order_by(PlaylistItem.position, PlaylistItem.id)
        )
        return list(db.execute(stmt).all())
