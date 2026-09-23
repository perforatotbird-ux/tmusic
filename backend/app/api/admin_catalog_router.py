"""
T-Music Admin Catalog API
Admin-only curation: rename artists/albums/collections, change kinds,
create/delete collections, move albums between collections.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_admin_user
from app.core.database import get_db
from app.models.auth import AppUser
from app.models.music import MusicArtist
from app.repositories.album_repository import AlbumRepository
from app.repositories.artist_repository import ArtistRepository
from app.schemas.library import MusicAlbumCard, MusicArtistCard
from app.services.music_library_service import (
    CatalogAdminService,
    library_service,
)

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
    user: AppUser = Depends(get_admin_user),
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
    user: AppUser = Depends(get_admin_user),
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
        parent = ArtistRepository.get_by_id(db, artist.parent_id)
        parent_name = parent.display_name if parent else None
    children_count = (
        db.execute(select(func.count(MusicArtist.id)).where(MusicArtist.parent_id == artist.id)).scalar() or 0
    )
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


@router.post("/artists", response_model=MusicArtistCard)
async def catalog_create_collection(
    body: CollectionCreate,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    artist = CatalogAdminService.create_collection(
        db, body.display_name, body.kind, parent_id=body.parent_id, bio=body.bio
    )
    parent_name = None
    if artist.parent_id:
        parent = ArtistRepository.get_by_id(db, artist.parent_id)
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
    user: AppUser = Depends(get_admin_user),
):
    return CatalogAdminService.delete_artist(db, artist_id)


@router.get("/albums", response_model=List[MusicAlbumCard])
async def catalog_albums(
    search: Optional[str] = Query(None),
    artist_id: Optional[int] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    if search in ("null", "undefined") or not (search or "").strip():
        search = None
    if artist_id is not None:
        albums = AlbumRepository.list_by_artist_id(db, artist_id, limit=limit, offset=offset)
        if search:
            needle = search.strip().lower()
            albums = [a for a in albums if needle in (a.title or "").lower()]
    else:
        albums = AlbumRepository.list_albums(db, search=search, limit=limit, offset=offset)
    result = []
    for alb in albums:
        cov_url, c_url = library_service._cover_pair(alb.cover_url, alb.custom_image_key, alb.enriched_at)
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
            enriched_at=alb.enriched_at,
        ))
    return result


@router.patch("/albums/{album_id}", response_model=MusicAlbumCard)
async def catalog_update_album(
    album_id: int,
    body: AlbumUpdate,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
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
