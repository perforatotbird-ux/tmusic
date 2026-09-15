"""
T-Music Library Service
Coordinates artist catalog, discographies, custom cover art, and library statistics.
"""
import urllib.parse
from typing import Optional, List
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.music import MusicArtist, MusicAlbum
from app.models.vfs import VFile
from app.repositories.artist_repository import ArtistRepository
from app.repositories.album_repository import AlbumRepository
from app.schemas.library import (
    MusicArtistCard, MusicAlbumCard, MusicArtistDetail, LibraryStats
)


class MusicLibraryService:
    """Business service for catalog navigation and metadata management."""

    @staticmethod
    def _build_custom_cover_url(key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        return f"/api/library/cover?key={key}&custom=1"

    @staticmethod
    def _build_cover_url(url: Optional[str], custom_key: Optional[str], enriched_at: Optional[datetime]) -> Optional[str]:
        if custom_key:
            return f"/api/library/cover?key={custom_key}&custom=1"
        if not url:
            return None
        stamp = int(enriched_at.timestamp()) if enriched_at else 1
        quoted_url = urllib.parse.quote(url, safe="")
        return f"/api/library/cover?url={quoted_url}&v={stamp}"

    @staticmethod
    def _album_sort_key(alb: MusicAlbum):
        year = alb.year
        return (year is None, year if year is not None else 0, alb.title or "")

    @classmethod
    def list_artists(
        cls,
        db: Session,
        kind: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 200,
        offset: int = 0
    ) -> List[MusicArtistCard]:
        artists = ArtistRepository.list_artists(db, kind=kind, search=search, limit=limit, offset=offset)
        result = []
        for a, album_count in artists:
            custom_url = cls._build_custom_cover_url(a.custom_image_key)
            img_url = custom_url or cls._build_cover_url(a.image_url, None, a.enriched_at)
            result.append(MusicArtistCard(
                id=a.id,
                folder_id=a.folder_id,
                kind=a.kind,
                display_name=a.display_name,
                image_url=img_url,
                custom_image_url=custom_url,
                confidence=a.confidence,
                album_count=album_count or 0,
                enriched_at=a.enriched_at
            ))
        return result

    @classmethod
    def list_albums(
        cls,
        db: Session,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[MusicAlbumCard]:
        albums = AlbumRepository.list_albums(db, search=search, limit=limit, offset=offset)
        result = []
        for alb in albums:
            c_url = cls._build_custom_cover_url(alb.custom_image_key)
            cov_url = c_url or cls._build_cover_url(alb.cover_url, None, alb.enriched_at)
            result.append(MusicAlbumCard(
                id=alb.id,
                folder_id=alb.folder_id,
                artist_id=alb.artist_id,
                title=alb.title,
                year=alb.year,
                mbid=alb.mbid,
                cover_url=cov_url,
                custom_image_url=c_url,
                confidence=alb.confidence,
                is_tracks=False,
                enriched_at=alb.enriched_at
            ))
        return result

    @classmethod
    def get_artist_detail(cls, db: Session, artist_id: int) -> MusicArtistDetail:
        artist = ArtistRepository.get_by_id(db, artist_id)
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found.")

        # Albums already eager-loaded via selectinload — no second query.
        albums = sorted(artist.albums or [], key=cls._album_sort_key)
        album_cards = []
        for alb in albums:
            c_url = cls._build_custom_cover_url(alb.custom_image_key)
            cov_url = c_url or cls._build_cover_url(alb.cover_url, None, alb.enriched_at)
            is_tracks = alb.folder_id == artist.folder_id and artist.kind != "standalone_album"

            album_cards.append(MusicAlbumCard(
                id=alb.id,
                folder_id=alb.folder_id,
                artist_id=alb.artist_id,
                title=alb.title,
                year=alb.year,
                mbid=alb.mbid,
                cover_url=cov_url,
                custom_image_url=c_url,
                confidence=alb.confidence,
                is_tracks=is_tracks,
                enriched_at=alb.enriched_at
            ))

        custom_art_url = cls._build_custom_cover_url(artist.custom_image_key)
        art_img = custom_art_url or cls._build_cover_url(artist.image_url, None, artist.enriched_at)

        return MusicArtistDetail(
            id=artist.id,
            folder_id=artist.folder_id,
            kind=artist.kind,
            display_name=artist.display_name,
            search_name=artist.search_name,
            mbid=artist.mbid,
            image_url=art_img,
            custom_image_url=custom_art_url,
            bio=artist.bio,
            wikipedia_url=artist.wikipedia_url,
            confidence=artist.confidence,
            locked=artist.locked,
            enriched_at=artist.enriched_at,
            albums=album_cards
        )

    @classmethod
    def get_stats(cls, db: Session) -> LibraryStats:
        """Single round-trip for all catalog aggregates."""
        audio = (VFile.is_trash.is_(False)) & (VFile.mime_type.like("audio/%"))
        row = db.execute(
            select(
                select(func.count(MusicArtist.id)).scalar_subquery(),
                select(func.count(MusicAlbum.id)).scalar_subquery(),
                select(func.count(VFile.id)).where(audio).scalar_subquery(),
                select(func.coalesce(func.sum(VFile.size), 0)).where(audio).scalar_subquery(),
            )
        ).one()
        return LibraryStats(
            artists_count=row[0] or 0,
            albums_count=row[1] or 0,
            tracks_count=row[2] or 0,
            total_size_bytes=int(row[3] or 0)
        )

    @staticmethod
    def set_custom_cover(db: Session, target_type: str, target_id: int, key: Optional[str]):
        if target_type == "artist":
            entity = ArtistRepository.get_by_id(db, target_id)
        elif target_type == "album":
            entity = AlbumRepository.get_by_id(db, target_id)
        else:
            raise HTTPException(status_code=400, detail="Invalid target_type. Must be 'artist' or 'album'.")

        if not entity:
            raise HTTPException(status_code=404, detail=f"{target_type.capitalize()} not found.")

        entity.custom_image_key = key
        entity.updated_at = datetime.utcnow()
        db.commit()


library_service = MusicLibraryService()
