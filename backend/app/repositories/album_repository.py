"""
T-Music Album Repository
Data access layer for MusicAlbum entities.
"""
from typing import Optional, List
from sqlalchemy import select, func
from sqlalchemy.orm import Session, load_only

from app.models.music import MusicAlbum
from app.utils.like import like_pattern


# Columns required by MusicAlbumCard (list view).
_ALBUM_CARD_COLUMNS = (
    MusicAlbum.id,
    MusicAlbum.folder_id,
    MusicAlbum.artist_id,
    MusicAlbum.title,
    MusicAlbum.year,
    MusicAlbum.mbid,
    MusicAlbum.cover_url,
    MusicAlbum.custom_image_key,
    MusicAlbum.confidence,
    MusicAlbum.enriched_at,
)


class AlbumRepository:
    """Repository managing MusicAlbum database operations."""

    @staticmethod
    def get_by_id(db: Session, album_id: int) -> Optional[MusicAlbum]:
        stmt = select(MusicAlbum).where(MusicAlbum.id == album_id)
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def get_by_folder_id(db: Session, folder_id: int) -> Optional[MusicAlbum]:
        stmt = select(MusicAlbum).where(MusicAlbum.folder_id == folder_id)
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_by_artist_id(
        db: Session,
        artist_id: int,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[MusicAlbum]:
        stmt = (
            select(MusicAlbum)
            .options(load_only(*_ALBUM_CARD_COLUMNS))
            .where(MusicAlbum.artist_id == artist_id)
            .order_by(MusicAlbum.year.asc().nulls_last(), MusicAlbum.title.asc())
        )
        if limit is not None:
            stmt = stmt.offset(offset).limit(limit)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def list_albums(
        db: Session,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[MusicAlbum]:
        stmt = (
            select(MusicAlbum)
            .options(load_only(*_ALBUM_CARD_COLUMNS))
        )
        if search and search not in ("null", "undefined") and search.strip():
            pattern = like_pattern(search)
            stmt = stmt.where(func.lower(MusicAlbum.title).like(pattern, escape="\\"))
        stmt = stmt.order_by(MusicAlbum.title.asc()).offset(offset).limit(limit)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def count(db: Session) -> int:
        stmt = select(func.count(MusicAlbum.id))
        return db.execute(stmt).scalar() or 0
