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
from app.utils.time import utcnow


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
            return None
        if not url:
            return None
        quoted_url = urllib.parse.quote(url, safe="")
        return f"/api/library/cover?url={quoted_url}"

    @classmethod
    def _cover_pair(cls, url: Optional[str], custom_key: Optional[str],
                    enriched_at: Optional[datetime]) -> tuple[Optional[str], Optional[str]]:
        """Returns (display_url, custom_url): custom cover wins over enriched URL."""
        custom_url = cls._build_custom_cover_url(custom_key)
        display_url = custom_url if custom_url else cls._build_cover_url(url, None, enriched_at)
        return display_url, custom_url

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
        for a, album_count, parent_name, children_count in artists:
            img_url, custom_url = cls._cover_pair(a.image_url, a.custom_image_key, a.enriched_at)
            result.append(MusicArtistCard(
                id=a.id,
                folder_id=a.folder_id,
                kind=a.kind,
                display_name=a.display_name,
                image_url=img_url,
                custom_image_url=custom_url,
                confidence=a.confidence,
                album_count=album_count or 0,
                parent_id=a.parent_id,
                parent_name=parent_name,
                children_count=children_count or 0,
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
            cov_url, c_url = cls._cover_pair(alb.cover_url, alb.custom_image_key, alb.enriched_at)
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
            cov_url, c_url = cls._cover_pair(alb.cover_url, alb.custom_image_key, alb.enriched_at)
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

        art_img, custom_art_url = cls._cover_pair(artist.image_url, artist.custom_image_key, artist.enriched_at)

        child_cards = [
            MusicArtistCard(
                id=child.id,
                folder_id=child.folder_id,
                kind=child.kind,
                display_name=child.display_name,
                image_url=cls._cover_pair(child.image_url, child.custom_image_key, child.enriched_at)[0],
                custom_image_url=cls._build_custom_cover_url(child.custom_image_key),
                confidence=child.confidence,
                album_count=0,
                parent_id=artist.id,
                parent_name=artist.display_name,
                children_count=0,
                enriched_at=child.enriched_at,
            )
            for child in ArtistRepository.list_children(db, artist.id)
        ]

        parent_name = None
        if artist.parent_id:
            parent = ArtistRepository.get_by_id(db, artist.parent_id)
            parent_name = parent.display_name if parent else None

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
            parent_id=artist.parent_id,
            parent_name=parent_name,
            enriched_at=artist.enriched_at,
            albums=album_cards,
            children=child_cards
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
        entity.updated_at = utcnow()
        db.commit()


class CatalogAdminService:
    """Admin-only catalog curation: renames, kinds, collections, album moves.

    Any manual edit sets locked=True on artists so automatic enrichment
    does not overwrite the admin's wording later.
    """

    VALID_KINDS = ("artist", "compilation", "standalone_album", "genre_collection")

    @classmethod
    def _validate_parent(cls, db: Session, artist_id: Optional[int], parent_id: Optional[int]) -> None:
        """Guard the self-referencing hierarchy: target exists, no self/descendant loops.

        `artist_id` is the card being moved (None while creating a new card,
        when only the target's existence can be checked).
        """
        if parent_id is None:
            return
        if artist_id is not None and parent_id == artist_id:
            raise HTTPException(status_code=400, detail="A card cannot be nested inside itself.")
        target = ArtistRepository.get_by_id(db, parent_id)
        if not target:
            raise HTTPException(status_code=404, detail="Target parent card not found.")
        if artist_id is not None and artist_id in ArtistRepository.ancestor_ids(db, parent_id):
            raise HTTPException(
                status_code=400,
                detail="Cannot nest a card inside its own descendant (cycle).",
            )

    @classmethod
    def update_artist(
        cls,
        db: Session,
        artist_id: int,
        display_name: Optional[str] = None,
        kind: Optional[str] = None,
        bio: Optional[str] = None,
        locked: Optional[bool] = None,
        parent_id: Optional[int] = None,
        set_parent: bool = False,
    ) -> MusicArtist:
        artist = ArtistRepository.get_by_id(db, artist_id)
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found.")

        touched = False
        if display_name is not None:
            name = display_name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="display_name must not be empty.")
            if len(name) > 200:
                raise HTTPException(status_code=400, detail="display_name is too long (max 200).")
            artist.display_name = name
            artist.search_name = name.lower()
            artist.locked = True
            touched = True
        if kind is not None:
            if kind not in cls.VALID_KINDS:
                raise HTTPException(status_code=400, detail=f"Invalid kind. Allowed: {', '.join(cls.VALID_KINDS)}.")
            artist.kind = kind
            touched = True
        if bio is not None:
            artist.bio = bio.strip() or None
            touched = True
        if locked is not None:
            artist.locked = bool(locked)
            touched = True
        if set_parent:
            # Re-parent (int) or detach to root (explicit null) — the drag-and-drop
            # curation primitive. Validated before the first write so a cycle
            # cannot leave the row half-updated.
            cls._validate_parent(db, artist.id, parent_id)
            if artist.parent_id != parent_id:
                artist.parent_id = parent_id
                touched = True
        if touched:
            artist.updated_at = utcnow()
            db.commit()
            db.refresh(artist)
        return artist

    @classmethod
    def create_collection(
        cls,
        db: Session,
        display_name: str,
        kind: str = "genre_collection",
        parent_id: Optional[int] = None,
        bio: Optional[str] = None,
    ) -> MusicArtist:
        from app.models.vfs import Folder, FolderClosure

        name = (display_name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="display_name must not be empty.")
        if len(name) > 200:
            raise HTTPException(status_code=400, detail="display_name is too long (max 200).")
        if kind not in cls.VALID_KINDS:
            raise HTTPException(status_code=400, detail=f"Invalid kind. Allowed: {', '.join(cls.VALID_KINDS)}.")

        folder = Folder(name=name)
        db.add(folder)
        db.flush()  # assign folder.id without committing
        db.add(FolderClosure(ancestor_id=folder.id, descendant_id=folder.id, depth=0))
        artist = MusicArtist(
            folder_id=folder.id,
            kind=kind,
            display_name=name,
            search_name=name.lower(),
            locked=True,  # hand-made entries are never auto-enriched
        )
        if bio and bio.strip():
            artist.bio = bio.strip()
        if parent_id is not None:
            # Validate before flush of the parent FK so a bad id rolls the
            # whole creation back instead of committing an orphan folder.
            cls._validate_parent(db, None, parent_id)
            artist.parent_id = parent_id
        db.add(artist)
        db.commit()
        db.refresh(artist)
        return artist

    @staticmethod
    def delete_artist(db: Session, artist_id: int) -> dict:
        """Delete an artist/collection entry. Refuses when albums or files remain."""
        from app.models.vfs import Folder, FolderClosure

        artist = ArtistRepository.get_by_id(db, artist_id)
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found.")

        album_count = db.execute(
            select(func.count(MusicAlbum.id)).where(MusicAlbum.artist_id == artist.id)
        ).scalar() or 0
        if album_count:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete: {album_count} album(s) still belong here. Move them first.",
            )
        file_count = db.execute(
            select(func.count(VFile.id)).where(VFile.folder_id == artist.folder_id)
        ).scalar() or 0
        if file_count:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete: {file_count} file(s) still reference this folder.",
            )

        folder_id = artist.folder_id
        # Detach nested cards first: they are independent catalog entries and
        # must survive the parent's deletion (never cascade into real artists).
        detached = db.execute(
            MusicArtist.__table__.update()
            .where(MusicArtist.parent_id == artist.id)
            .values(parent_id=None)
        ).rowcount
        db.delete(artist)
        db.flush()
        # Remove the backing folder only if nothing else references it.
        still_used = (
            db.execute(select(func.count(MusicArtist.id)).where(MusicArtist.folder_id == folder_id)).scalar() or 0
        ) + (
            db.execute(select(func.count(MusicAlbum.id)).where(MusicAlbum.folder_id == folder_id)).scalar() or 0
        )
        if not still_used:
            db.execute(FolderClosure.__table__.delete().where(
                (FolderClosure.ancestor_id == folder_id) | (FolderClosure.descendant_id == folder_id)
            ))
            folder = db.get(Folder, folder_id)
            if folder:
                db.delete(folder)
        db.commit()
        return {"ok": True, "id": artist_id, "detached_children": detached or 0}

    @staticmethod
    def update_album(
        db: Session,
        album_id: int,
        title: Optional[str] = None,
        year: Optional[int] = None,
        artist_id: Optional[int] = None,
    ) -> MusicAlbum:
        album = AlbumRepository.get_by_id(db, album_id)
        if not album:
            raise HTTPException(status_code=404, detail="Album not found.")

        touched = False
        if title is not None:
            name = title.strip()
            if not name:
                raise HTTPException(status_code=400, detail="title must not be empty.")
            if len(name) > 300:
                raise HTTPException(status_code=400, detail="title is too long (max 300).")
            album.title = name
            touched = True
        if year is not None:
            if year == 0:
                album.year = None
                touched = True
            elif 1800 <= year <= 2100:
                album.year = year
                touched = True
            else:
                raise HTTPException(status_code=400, detail="year must be between 1800 and 2100 (or 0 to clear).")
        if artist_id is not None:
            if artist_id == album.artist_id:
                pass
            else:
                target = ArtistRepository.get_by_id(db, artist_id)
                if not target:
                    raise HTTPException(status_code=404, detail="Target artist/collection not found.")
                album.artist_id = target.id
                touched = True
        if touched:
            album.updated_at = utcnow()
            db.commit()
            db.refresh(album)
        return album


library_service = MusicLibraryService()
