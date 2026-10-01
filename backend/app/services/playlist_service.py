"""
T-Music Playlist Service
"""
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.auth import AppUser
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
    def get_playlist(db: Session, user_id: int, playlist_id: int, *, for_update=False) -> Optional[Playlist]:
        stmt = select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user_id)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        else:
            stmt = stmt.options(selectinload(Playlist.items))
        return db.scalar(stmt)

    @staticmethod
    def mirror(db: Session, user_id: int, name: str, file_ids: List[int]) -> Playlist:
        """Find/create + replace in one transaction; invalid IDs preserve the old list."""
        normalized = name.strip()
        if not normalized or len(normalized) > 120 or len(file_ids) > 5000 or len(set(file_ids)) != len(file_ids):
            raise HTTPException(status_code=400, detail="Invalid playlist data.")
        try:
            # Serializes simultaneous mirrors even when the playlist doesn't exist yet.
            owner = db.scalar(select(AppUser).where(AppUser.id == user_id).with_for_update())
            if owner is None:
                raise HTTPException(status_code=404, detail="User not found.")
            existing = db.scalars(select(Playlist).where(Playlist.user_id == user_id).order_by(Playlist.id)).all()
            pl = next((p for p in existing if p.name.casefold() == normalized.casefold()), None)
            valid = set(db.scalars(select(VFile.id).where(VFile.id.in_(file_ids), VFile.is_trash == False)).all())
            if valid != set(file_ids):
                raise HTTPException(status_code=404, detail="Track not found.")
            if pl is None:
                pl = Playlist(user_id=user_id, name=normalized)
                db.add(pl)
                db.flush()
            else:
                pl = PlaylistService.get_playlist(db, user_id, pl.id, for_update=True)
            db.execute(delete(PlaylistItem).where(PlaylistItem.playlist_id == pl.id))
            db.add_all([PlaylistItem(playlist_id=pl.id, file_id=file_id, position=position)
                        for position, file_id in enumerate(file_ids, start=1)])
            pl.updated_at = utcnow()
            db.flush()
            db.commit()
            return pl
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def create_playlist(db: Session, user_id: int, name: str, initial_file_id: Optional[int] = None) -> Playlist:
        normalized = name.strip() if isinstance(name, str) else ""
        if not normalized or len(normalized) > 120:
            raise HTTPException(status_code=400, detail="Playlist name must contain 1 to 120 characters.")
        try:
            if initial_file_id is not None:
                track = db.scalar(select(VFile).where(VFile.id == initial_file_id, VFile.is_trash.is_(False)))
                if track is None:
                    raise HTTPException(status_code=404, detail="Track not found.")
            pl = Playlist(user_id=user_id, name=normalized)
            db.add(pl)
            db.flush()
            if initial_file_id is not None:
                db.add(PlaylistItem(playlist_id=pl.id, file_id=initial_file_id, position=1))
            db.flush()
            db.commit()
            db.refresh(pl)
            return pl
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def delete_playlist(db: Session, user_id: int, playlist_id: int) -> bool:
        pl = db.scalar(
            select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user_id).with_for_update()
        )
        if not pl:
            return False
        db.delete(pl)
        db.commit()
        return True

    @staticmethod
    def add_track(db: Session, user_id: int, playlist_id: int, file_id: int) -> Optional[PlaylistItem]:
        pl = PlaylistService.get_playlist(db, user_id, playlist_id, for_update=True)
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
        pl = PlaylistService.get_playlist(db, user_id, playlist_id, for_update=True)
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

    @staticmethod
    def reorder_items(
        db: Session, user_id: int, playlist_id: int, item_ids: List[int]
    ) -> bool:
        """Persist a full permutation of playlist items (position = list order)."""
        pl = PlaylistService.get_playlist(db, user_id, playlist_id, for_update=True)
        if not pl:
            return False
        rows = list(
            db.scalars(
                select(PlaylistItem).where(PlaylistItem.playlist_id == playlist_id)
            ).all()
        )
        current_ids = {row.id for row in rows}
        if len(item_ids) != len(set(item_ids)) or set(item_ids) != current_ids:
            return False
        by_id = {row.id: row for row in rows}
        for pos, item_id in enumerate(item_ids, start=1):
            by_id[item_id].position = pos
        pl.updated_at = utcnow()
        db.commit()
        return True


playlist_service = PlaylistService()
