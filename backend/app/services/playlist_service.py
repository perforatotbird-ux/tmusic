"""
T-Music Playlist Service

Business logic for user playlists. All DB access goes through PlaylistRepository;
this layer owns validation and the transaction boundary.
"""
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.playlist import Playlist, PlaylistItem
from app.models.vfs import VFile
from app.repositories.playlist_repository import PlaylistRepository
from app.utils.time import utcnow


class PlaylistService:
    @staticmethod
    def list_playlists(db: Session, user_id: int) -> List[Tuple[Playlist, int]]:
        return PlaylistRepository.list_with_counts(db, user_id)

    @staticmethod
    def get_playlist(db: Session, user_id: int, playlist_id: int, *, for_update=False) -> Optional[Playlist]:
        return PlaylistRepository.get(db, user_id, playlist_id, for_update=for_update)

    @staticmethod
    def mirror(db: Session, user_id: int, name: str, file_ids: List[int]) -> Playlist:
        """Find/create + replace in one transaction; invalid IDs preserve the old list."""
        normalized = name.strip()
        if not normalized or len(normalized) > 120 or len(file_ids) > 5000 or len(set(file_ids)) != len(file_ids):
            raise HTTPException(status_code=400, detail="Invalid playlist data.")
        try:
            # Serializes simultaneous mirrors even when the playlist doesn't exist yet.
            owner = PlaylistRepository.lock_user(db, user_id)
            if owner is None:
                raise HTTPException(status_code=404, detail="User not found.")
            existing = PlaylistRepository.list_by_user(db, user_id)
            pl = next((p for p in existing if p.name.casefold() == normalized.casefold()), None)
            valid = PlaylistRepository.valid_file_ids(db, file_ids)
            if valid != set(file_ids):
                raise HTTPException(status_code=404, detail="Track not found.")
            if pl is None:
                pl = Playlist(user_id=user_id, name=normalized)
                db.add(pl)
                db.flush()
            else:
                pl = PlaylistService.get_playlist(db, user_id, pl.id, for_update=True)
            PlaylistRepository.delete_items(db, pl.id)
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
                track = PlaylistRepository.get_vfile(db, initial_file_id)
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
        pl = PlaylistRepository.get(db, user_id, playlist_id, for_update=True)
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
        exists = PlaylistRepository.get_item(db, playlist_id, file_id)
        if exists:
            return exists
        next_pos = PlaylistRepository.max_position(db, playlist_id)
        item = PlaylistItem(
            playlist_id=playlist_id,
            file_id=file_id,
            position=next_pos + 1,
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
        item = PlaylistRepository.get_item_by_id(db, playlist_id, item_id)
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
        return PlaylistRepository.tracks(db, playlist_id)

    @staticmethod
    def reorder_items(
        db: Session, user_id: int, playlist_id: int, item_ids: List[int]
    ) -> bool:
        """Persist a full permutation of playlist items (position = list order)."""
        pl = PlaylistService.get_playlist(db, user_id, playlist_id, for_update=True)
        if not pl:
            return False
        rows = PlaylistRepository.list_items(db, playlist_id)
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
