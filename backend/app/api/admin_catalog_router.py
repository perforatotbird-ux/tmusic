"""
T-Music Admin Catalog API
Admin-only curation: rename artists/albums/collections, change kinds,
create/delete collections, move albums between collections.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_catalog_user
from app.core.database import get_db
from app.models.auth import AppUser
from app.services import lastfm_service
from app.utils.time import utcnow
from app.schemas.library import MusicAlbumCard, MusicArtistCard
from app.services.music_library_service import (
    CatalogAdminService,
    library_service,
)

logger = logging.getLogger("tmusic.admin.catalog")

router = APIRouter(prefix="/api/admin/catalog", tags=["admin-catalog"])


class ArtistUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=200)
    kind: Optional[str] = None
    bio: Optional[str] = Field(default=None, max_length=5000)
    locked: Optional[bool] = None
    # Drag-and-drop re-parenting: int moves the card, explicit null detaches
    # it to the catalog root. Field absence means "do not touch".
    parent_id: Optional[int] = None


class CollectionCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="genre_collection")
    parent_id: Optional[int] = None
    bio: Optional[str] = Field(default=None, max_length=5000)


class ArtistBatchUpdate(BaseModel):
    artist_ids: List[int] = Field(min_length=1, max_length=500)
    kind: Optional[str] = None
    locked: Optional[bool] = None
    parent_id: Optional[int] = None


class AlbumBatchUpdate(BaseModel):
    album_ids: List[int] = Field(min_length=1, max_length=1000)
    artist_id: Optional[int] = None
    year: Optional[int] = Field(default=None, ge=0, le=2100)


class AlbumUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    year: Optional[int] = None
    artist_id: Optional[int] = None


@router.get("/artists", response_model=List[MusicArtistCard])
async def catalog_artists(
    kind: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    if kind in ("null", "undefined", ""):
        kind = None
    if search in ("null", "undefined") or not (search or "").strip():
        search = None
    return library_service.list_artists(db, kind=kind, search=search, limit=limit, offset=offset)


@router.patch("/artists/{artist_id}", response_model=MusicArtistCard)
async def catalog_update_artist(
    artist_id: int,
    body: ArtistUpdate,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    artist = CatalogAdminService.update_artist(
        db,
        artist_id,
        display_name=body.display_name,
        kind=body.kind,
        bio=body.bio,
        locked=body.locked,
        parent_id=body.parent_id,
        # An explicit null in the payload is a meaningful "detach to root";
        # model_fields_set tells it apart from a simply omitted field.
        set_parent="parent_id" in body.model_fields_set,
    )
    parent_name = None
    if artist.parent_id:
        parent = library_service.get_artist(db, artist.parent_id)
        parent_name = parent.display_name if parent else None
    children_count = library_service.count_artist_children(db, artist.id)
    cards = library_service.list_artists(db, search=artist.display_name, limit=500)
    for card in cards:
        if card.id == artist.id:
            return card
    # Fallback: rebuild the card directly if search missed (e.g. immediate index lag).
    img_url, custom_url = library_service._cover_pair(
        artist.image_url, artist.custom_image_key, artist.enriched_at
    )
    return MusicArtistCard(
        id=artist.id,
        folder_id=artist.folder_id,
        kind=artist.kind,
        display_name=artist.display_name,
        image_url=img_url,
        custom_image_url=custom_url,
        confidence=artist.confidence,
        album_count=len(artist.albums or []),
        parent_id=artist.parent_id,
        parent_name=parent_name,
        children_count=children_count,
        enriched_at=artist.enriched_at,
    )


@router.post("/artists/{artist_id}/scan")
async def catalog_scan_artist(
    artist_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    if not lastfm_service.metadata_ready():
        raise HTTPException(status_code=503, detail="Last.fm metadata is not configured.")
    artist = library_service.get_artist(db, artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found.")
    info = lastfm_service.get_artist_info(artist.display_name)
    if not info:
        raise HTTPException(status_code=404, detail="Artist was not found on Last.fm.")
    artist.bio = info.get("summary") or artist.bio
    artist.mbid = info.get("mbid") or artist.mbid
    if info.get("image_url"):
        artist.image_url = info["image_url"]
    artist.enriched_at = utcnow()
    artist.locked = True
    artist.updated_at = utcnow()
    db.commit()
    # Warm the local disk cache once, so cards served via ?key= hit local
    # files and never trigger a per-request remote fetch on display.
    if info.get("image_url"):
        try:
            from app.services.cover_cache_service import cover_cache

            await cover_cache.fetch_and_cache(info["image_url"])
        except Exception:
            logger.debug("Cover cache warm failed for artist %s", artist.id, exc_info=True)
    return {"ok": True, "id": artist.id, "updated": True, "mbid": artist.mbid}


@router.post("/artists", response_model=MusicArtistCard)
async def catalog_create_collection(
    body: CollectionCreate,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    artist = CatalogAdminService.create_collection(
        db, body.display_name, body.kind, parent_id=body.parent_id, bio=body.bio
    )
    parent_name = None
    if artist.parent_id:
        parent = library_service.get_artist(db, artist.parent_id)
        parent_name = parent.display_name if parent else None
    return MusicArtistCard(
        id=artist.id,
        folder_id=artist.folder_id,
        kind=artist.kind,
        display_name=artist.display_name,
        image_url=None,
        custom_image_url=None,
        confidence=0,
        album_count=0,
        parent_id=artist.parent_id,
        parent_name=parent_name,
        children_count=0,
        enriched_at=None,
    )


@router.delete("/artists/{artist_id}")
async def catalog_delete_artist(
    artist_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    return CatalogAdminService.delete_artist(db, artist_id)


@router.get("/albums", response_model=List[MusicAlbumCard])
async def catalog_albums(
    search: Optional[str] = Query(None),
    artist_id: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    if search in ("null", "undefined") or not (search or "").strip():
        search = None
    return library_service.list_albums(db, search=search, artist_id=artist_id, limit=limit, offset=offset)


@router.patch("/albums/{album_id}", response_model=MusicAlbumCard)
async def catalog_update_album(
    album_id: int,
    body: AlbumUpdate,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    album = CatalogAdminService.update_album(
        db, album_id, title=body.title, year=body.year, artist_id=body.artist_id
    )
    cov_url, c_url = library_service._cover_pair(album.cover_url, album.custom_image_key, album.enriched_at)
    return MusicAlbumCard(
        id=album.id,
        folder_id=album.folder_id,
        artist_id=album.artist_id,
        title=album.title,
        year=album.year,
        mbid=album.mbid,
        cover_url=cov_url,
        custom_image_url=c_url,
        confidence=album.confidence,
        is_tracks=False,
        enriched_at=album.enriched_at,
    )


@router.post("/albums/{album_id}/scan")
async def catalog_scan_album(
    album_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    if not lastfm_service.metadata_ready():
        raise HTTPException(status_code=503, detail="Last.fm metadata is not configured.")
    album = library_service.get_album(db, album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album not found.")
    artist = library_service.get_artist(db, album.artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Album artist not found.")
    file = library_service.first_album_track(db, album.folder_id)
    if not file:
        raise HTTPException(status_code=404, detail="Album has no playable track for enrichment.")
    track_artist, track_title, _ = lastfm_service.parse_track_meta(
        file.name,
        artist_hint=artist.display_name,
        album_hint=album.title,
    )
    info = lastfm_service.get_track_info(track_artist, track_title, album.title)
    if not info:
        raise HTTPException(status_code=404, detail="Album track was not found on Last.fm.")
    album.mbid = info.get("mbid") or album.mbid
    if info.get("image_url"):
        album.cover_url = info["image_url"]
    album.enriched_at = utcnow()
    album.updated_at = utcnow()
    db.commit()
    # Warm the local disk cache once, so cards served via ?key= hit local
    # files and never trigger a per-request remote fetch on display.
    if info.get("image_url"):
        try:
            from app.services.cover_cache_service import cover_cache

            await cover_cache.fetch_and_cache(info["image_url"])
        except Exception:
            logger.debug("Cover cache warm failed for album %s", album.id, exc_info=True)
    return {"ok": True, "id": album.id, "updated": True, "mbid": album.mbid}


@router.post("/artists/batch")
async def catalog_batch_update_artists(
    body: ArtistBatchUpdate,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    updated = 0
    for artist_id in body.artist_ids:
        CatalogAdminService.update_artist(
            db,
            artist_id,
            kind=body.kind,
            locked=body.locked,
            parent_id=body.parent_id,
            set_parent="parent_id" in body.model_fields_set,
        )
        updated += 1
    return {"ok": True, "updated": updated}


@router.post("/albums/batch")
async def catalog_batch_update_albums(
    body: AlbumBatchUpdate,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    updated = 0
    for album_id in body.album_ids:
        CatalogAdminService.update_album(
            db,
            album_id,
            year=body.year,
            artist_id=body.artist_id,
        )
        updated += 1
    return {"ok": True, "updated": updated}
