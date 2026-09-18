"""
T-Music Playlist Service
"""
from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.playlist import Playlist, PlaylistItem
from app.models.vfs import VFile
from app.utils.time import utcnow


class PlaylistService:
    @staticmethod
    def list_playlists(db: Session, user_id: int) -> List[Tuple[Playlist, int]]:
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
    def get_playlist(db: Session, user_id: int, playlist_id: int) -> Optional[Playlist]:
        return db.scalar(
            select(Playlist)
            .options(selectinload(Playlist.items))
            .where(Playlist.id == playlist_id, Playlist.user_id == user_id)
        )

    @staticmethod
    def create_playlist(db: Session, user_id: int, name: str) -> Playlist:
        pl = Playlist(user_id=user_id, name=name.strip())
        db.add(pl)
        db.commit()
        db.refresh(pl)
        return pl

    @staticmethod
    def delete_playlist(db: Session, user_id: int, playlist_id: int) -> bool:
        pl = db.scalar(
            select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user_id)
        )
        if not pl:
            return False
        db.delete(pl)
        db.commit()
        return True

    @staticmethod
    def add_track(db: Session, user_id: int, playlist_id: int, file_id: int) -> Optional[PlaylistItem]:
        pl = PlaylistService.get_playlist(db, user_id, playlist_id)
        if not pl:
            return None
        exists = db.scalar(
            select(PlaylistItem).where(
                PlaylistItem.playlist_id == playlist_id,
                PlaylistItem.file_id == file_id,
            )
        )
        if exists:
            return exists
        next_pos = db.scalar(
            select(func.coalesce(func.max(PlaylistItem.position), 0)).where(
                PlaylistItem.playlist_id == playlist_id
            )
        )
        item = PlaylistItem(
            playlist_id=playlist_id,
            file_id=file_id,
            position=int(next_pos or 0) + 1,
            added_at=utcnow(),
        )
        db.add(item)
        pl.updated_at = utcnow()
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def remove_track(db: Session, user_id: int, playlist_id: int, item_id: int) -> bool:
        pl = PlaylistService.get_playlist(db, user_id, playlist_id)
        if not pl:
            return False
        item = db.scalar(
            select(PlaylistItem).where(
                PlaylistItem.id == item_id,
                PlaylistItem.playlist_id == playlist_id,
            )
        )
        if not item:
            return False
        db.delete(item)
        pl.updated_at = utcnow()
        db.commit()
        return True

    @staticmethod
    def playlist_tracks(
        db: Session, playlist_id: int
    ) -> List[Tuple[PlaylistItem, Optional[VFile]]]:
        stmt = (
            select(PlaylistItem, VFile)
            .outerjoin(VFile, VFile.id == PlaylistItem.file_id)
            .where(PlaylistItem.playlist_id == playlist_id)
            .order_by(PlaylistItem.position, PlaylistItem.id)
        )
        return list(db.execute(stmt).all())


playlist_service = PlaylistService()
