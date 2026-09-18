"""
T-Music Artist Repository
Data access layer for MusicArtist entities.
"""
from typing import Optional, List, Tuple
from sqlalchemy import select, func
from sqlalchemy.orm import Session, selectinload, load_only

from app.models.music import MusicArtist, MusicAlbum
from app.utils.like import like_pattern


# Columns required by MusicArtistCard (list view) — avoids loading bio/wiki Text blobs.
_ARTIST_CARD_COLUMNS = (
    MusicArtist.id,
    MusicArtist.folder_id,
    MusicArtist.kind,
    MusicArtist.display_name,
    MusicArtist.custom_image_key,
    MusicArtist.image_url,
    MusicArtist.confidence,
    MusicArtist.enriched_at,
)


class ArtistRepository:
    """Repository managing MusicArtist database operations."""

    @staticmethod
    def get_by_id(db: Session, artist_id: int) -> Optional[MusicArtist]:
        stmt = (
            select(MusicArtist)
            .where(MusicArtist.id == artist_id)
            .options(selectinload(MusicArtist.albums))
        )
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def get_by_folder_id(db: Session, folder_id: int) -> Optional[MusicArtist]:
        stmt = select(MusicArtist).where(MusicArtist.folder_id == folder_id)
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_artists(
        db: Session,
        kind: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 200,
        offset: int = 0
    ) -> List[Tuple[MusicArtist, int]]:
        """List artists with album_count via correlated subquery (no full album load)."""
        album_count = (
            select(func.count(MusicAlbum.id))
            .where(MusicAlbum.artist_id == MusicArtist.id)
            .correlate(MusicArtist)
            .scalar_subquery()
            .label("album_count")
        )
        stmt = (
            select(MusicArtist, album_count)
            .options(load_only(*_ARTIST_CARD_COLUMNS))
        )
        if kind and kind not in ("null", "undefined"):
            stmt = stmt.where(MusicArtist.kind == kind)
        if search and search not in ("null", "undefined") and search.strip():
            pattern = like_pattern(search)
            stmt = stmt.where(
                func.lower(MusicArtist.display_name).like(pattern, escape="\\") |
                func.lower(MusicArtist.search_name).like(pattern, escape="\\")
            )
        stmt = stmt.order_by(MusicArtist.display_name.asc()).offset(offset).limit(limit)
        return list(db.execute(stmt).all())

    @staticmethod
    def count(db: Session, kind: Optional[str] = None) -> int:
        stmt = select(func.count(MusicArtist.id))
        if kind:
            stmt = stmt.where(MusicArtist.kind == kind)
        return db.execute(stmt).scalar() or 0
