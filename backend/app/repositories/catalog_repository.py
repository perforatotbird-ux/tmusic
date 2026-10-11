"""
T-Music Catalog Repository — aggregate library statistics.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.music import MusicAlbum, MusicArtist
from app.models.vfs import VFile


class CatalogRepository:
    @staticmethod
    def stats(db: Session):
        """Single round-trip: (artists, albums, tracks, total_bytes)."""
        audio = (VFile.is_trash.is_(False)) & (VFile.mime_type.like("audio/%"))
        return db.execute(
            select(
                select(func.count(MusicArtist.id)).scalar_subquery(),
                select(func.count(MusicAlbum.id)).scalar_subquery(),
                select(func.count(VFile.id)).where(audio).scalar_subquery(),
                select(func.coalesce(func.sum(VFile.size), 0)).where(audio).scalar_subquery(),
            )
        ).one()
