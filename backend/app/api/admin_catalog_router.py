"""T-Music Admin Catalog API

Admin-only curation: rename artists/albums/collections, change kinds,
create/delete collections, move albums between collections, refresh artwork.

Artist scans are background jobs with pollable progress — see
`app/services/scan_service.py` for why the old blocking request read as
«ничего не выполняет».
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
from app.services.music_library_service import (
    CatalogAdminService,
    library_service,
)
from app.services.scan_service import (
    ScanBusy,
    get_job,
    start_artist_scan,
)
from app.utils.time import utcnow
from app.schemas.library import MusicAlbumCard, MusicArtistCard

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
    root_only: Optional[bool] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    if kind in ("null", "undefined", ""):
        kind = None
    if search in ("null", "undefined") or not (search or "").strip():
        search = None
    return library_service.list_artists(
        db, kind=kind, search=search, root_only=root_only, limit=limit, offset=offset
    )


@router.post("/sync-folder/{folder_id}")
async def catalog_sync_folder(
    folder_id: int,
    catalog_kind: str = Query("compilation"),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    """Recursively synchronize an arbitrary nested folder tree into catalog artists and albums."""
    return CatalogAdminService.sync_catalog_folder_tree(db, folder_id, catalog_kind=catalog_kind)


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


# ── Scans: background jobs with progress ────────────────────────────────────
# A scan can take tens of seconds (up to MAX_ALBUMS_PER_SCAN Last.fm album
# lookups). Doing that inline used to leave the admin staring at a button with
# no counter and no stage, which is exactly why the action read as dead. The
# work now lives in scan_service and is polled through /scan/{job_id}.


@router.post("/artists/{artist_id}/scan")
async def catalog_scan_artist(
    artist_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    """Start refreshing one artist from Last.fm. Returns the job snapshot.

    202 + `{job_id, status, progress}` — poll `GET /scan/{job_id}` for progress.

    * **404** — no such artist
    * **409** — this artist is already being scanned, or too many scans run
    * **503** — Last.fm is not configured
    """
    artist = library_service.get_artist(db, artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found.")
    if not lastfm_service.metadata_ready():
        raise HTTPException(status_code=503, detail="Last.fm metadata is not configured.")
    try:
        job = start_artist_scan(artist.id, artist.display_name, user.id)
    except ScanBusy as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    return {**job, "accepted": True}


@router.get("/scan/{job_id}")
async def catalog_scan_status(
    job_id: str,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_catalog_user),
):
    """Progress of one scan job. `status` ∈ running | completed | failed."""
    job = get_job(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="Scan job not found.")
    return job.snapshot()


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
    """Refresh one album's artwork/MBID from Last.fm (album scan level).

    Album artwork is still real on Last.fm, unlike artist artwork. A hand-picked
    cover is never overwritten — only the enriched `cover_url` is refreshed.
    """
    if not lastfm_service.metadata_ready():
        raise HTTPException(status_code=503, detail="Last.fm metadata is not configured.")
    album = library_service.get_album(db, album_id)
    if not album:
        raise HTTPException(status_code=404, detail="Album not found.")
    artist = library_service.get_artist(db, album.artist_id)
    if not artist:
        raise HTTPException(status_code=404, detail="Album artist not found.")

    info = lastfm_service.get_album_info(artist.display_name, album.title, force=True)
    if not info or not info.get("image_url"):
        # Fall back to the album's first track: track.getInfo carries the album
        # artwork too, and it is the only route when album.getInfo has no entry.
        file = library_service.first_album_track(db, album.folder_id)
        if not file:
            raise HTTPException(status_code=404, detail="Album was not found on Last.fm.")
        track_artist, track_title, _ = lastfm_service.parse_track_meta(
            file.name,
            artist_hint=artist.display_name,
            album_hint=album.title,
        )
        info = lastfm_service.get_track_info(track_artist, track_title, album.title, force=True)
    if not info:
        raise HTTPException(status_code=404, detail="Album was not found on Last.fm.")

    fields: List[str] = []
    if info.get("mbid") and info["mbid"] != album.mbid:
        album.mbid = info["mbid"]
        fields.append("mbid")
    if info.get("image_url") and not album.custom_image_key and info["image_url"] != album.cover_url:
        album.cover_url = info["image_url"]
        fields.append("cover_url")

    if fields:
        album.enriched_at = utcnow()
        album.updated_at = utcnow()
        db.commit()
        db.refresh(album)
        # Warm the local disk cache once, so cards served via ?key= hit local
        # files and never trigger a per-request remote fetch on display.
        if "cover_url" in fields:
            try:
                from app.services.cover_cache_service import cover_cache

                await cover_cache.fetch_and_cache(album.cover_url)
            except Exception:
                logger.debug("Cover cache warm failed for album %s", album.id, exc_info=True)

    return {
        "ok": True,
        "id": album.id,
        "title": album.title,
        "updated": bool(fields),
        "fields": fields,
        "mbid": album.mbid,
        "cover_url": album.cover_url,
    }


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
