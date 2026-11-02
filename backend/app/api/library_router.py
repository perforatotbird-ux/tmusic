"""
T-Music Library API Router
Endpoints for artist lists, album discographies, direct album catalog, statistics, and secure cover proxying.
"""
import re
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, Response as RawResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.database import get_db
from app.api.dependencies import get_admin_user, get_current_user
from app.models.auth import AppUser
from app.schemas.library import (
    MusicArtistCard, MusicArtistDetail, MusicAlbumCard, LibraryStats
)
from app.services.music_library_service import library_service
from app.services.cover_cache_service import cover_cache, is_safe_host, MAX_IMAGE_BYTES
from app.utils.http import cover_cache_headers, cover_etag

router = APIRouter(prefix="/api/library", tags=["library"])

# Hard cap on multipart uploads: read is bounded so a giant file cannot OOM the worker.
# The 10 MB image check happens after decode; this cap only bounds RAM during upload.
MAX_UPLOAD_BYTES = MAX_IMAGE_BYTES + 1


@router.get("/artists", response_model=List[MusicArtistCard])
async def get_artists(
    kind: Optional[str] = Query(None, description="artist, compilation, standalone_album, genre_collection"),
    search: Optional[str] = Query(None, description="Search query"),
    root_only: Optional[bool] = Query(None, description="Filter to root artists only (parent_id IS NULL)"),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user)
):
    """Retrieve catalog artists with pagination and search."""
    # Guard against clients that serialize JS null/undefined into the query string.
    if kind in ("null", "undefined", ""):
        kind = None
    if search in ("null", "undefined") or not (search or "").strip():
        search = None
    if root_only is None and not search:
        root_only = True
    return library_service.list_artists(
        db, kind=kind, search=search, root_only=root_only, limit=limit, offset=offset
    )


@router.get("/albums", response_model=List[MusicAlbumCard])
async def get_albums(
    search: Optional[str] = Query(None, description="Search query"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user)
):
    """Retrieve all catalog albums with pagination and search."""
    if search in ("null", "undefined") or not (search or "").strip():
        search = None
    return library_service.list_albums(db, search=search, limit=limit, offset=offset)


@router.get("/artists/{artist_id}", response_model=MusicArtistDetail)
async def get_artist_detail(
    artist_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user)
):
    """Retrieve detailed artist card with albums, track cards, and biography."""
    return library_service.get_artist_detail(db, artist_id)


@router.get("/stats", response_model=LibraryStats)
async def get_library_stats(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user)
):
    """Get high-level statistics of the audio catalog."""
    return library_service.get_stats(db)


# Key charset for the manifest endpoint: mirrors save_custom_cover keys
# (32 hex chars + optional image extension) and CloudByte cache keys.
_MANIFEST_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MANIFEST_MAX_KEYS = 500


@router.get("/covers/manifest")
async def get_covers_manifest(
    keys: str = Query(..., description="Comma-separated cache keys, max 500"),
    custom: int = Query(0, description="1 to look in the custom-cover dir first"),
    user: AppUser = Depends(get_current_user),
):
    """Batch existence probe so the grid can skip hopeless cover fetches.

    Returns {"found": {key: etag}, "missing": [key]} using filesystem stats
    only (no file content is read). Auth required like /cover itself — an open
    endpoint would allow unauthenticated cache-key enumeration.
    """
    raw = [k.strip() for k in keys.split(",") if k.strip()][: _MANIFEST_MAX_KEYS + 1]
    if len(raw) > _MANIFEST_MAX_KEYS:
        raise HTTPException(status_code=400, detail=f"Too many keys (max {_MANIFEST_MAX_KEYS}).")
    if not raw:
        raise HTTPException(status_code=400, detail="No keys provided.")
    for k in raw:
        if not _MANIFEST_KEY_RE.match(k):
            # Fixed text: never echo client input back in an error detail.
            raise HTTPException(status_code=400, detail="Invalid key format.")

    is_custom = custom == 1
    # Up to 500 keys x several stat() calls each: keep the filesystem walk off
    # the event loop, otherwise it stalls in-flight audio streams for seconds.
    return await run_in_threadpool(_probe_cached_covers, raw, is_custom)


def _probe_cached_covers(keys: List[str], is_custom: bool) -> dict:
    found = {}
    missing = []
    for k in keys:
        path = cover_cache.get_cached_path(k, is_custom=is_custom)
        if path:
            found[k] = cover_etag(k)
        else:
            missing.append(k)
    return {"found": found, "missing": missing}


@router.get("/cover")
async def get_cover(
    key: Optional[str] = Query(None, description="Direct cached key"),
    url: Optional[str] = Query(None, description="Remote URL to fetch and cache (explicit fetch only; cards use key)"),
    custom: Optional[int] = Query(0, description="1 if custom uploaded cover"),
    user: AppUser = Depends(get_current_user)
):
    """
    Secure cover proxy with permanent caching headers and SSRF defense.
    Requires authentication — an open proxy would allow unauthenticated
    SSRF probing and cache-key enumeration.
    Supports both migrated CloudByte cache (.bin/.meta) and newly fetched images.

    Cache-only display contract (2026-10-03): artist/album/search cards emit
    ``?key=`` URLs derived from the stored remote URL, so normal display
    never touches the network — it serves the local disk cache or 404s.
    The ``?url=`` branch below is an explicit-fetch path (legacy clients,
    one-off warming); it checks the disk cache first and downloads only on
    miss. New display code must not emit ``?url=``.
    """
    if key:
        is_custom = custom == 1
        cached = await run_in_threadpool(cover_cache.get_cached_item, key, is_custom=is_custom)
        if cached:
            data, mime = cached
            headers = cover_cache_headers(key)
            return RawResponse(content=data, media_type=mime, headers=headers)

        path = await run_in_threadpool(cover_cache.get_cached_path, key, is_custom=is_custom)
        if path:
            headers = cover_cache_headers(key)
            return FileResponse(path, headers=headers)
        raise HTTPException(status_code=404, detail="Image not found in cache.")

    if url:
        cache_key = cover_cache.url_to_key(url)
        cached = await run_in_threadpool(cover_cache.get_cached_item, cache_key, is_custom=False)
        if cached:
            data, mime = cached
            headers = cover_cache_headers(cache_key)
            return RawResponse(content=data, media_type=mime, headers=headers)

        data, mime = await cover_cache.fetch_and_cache(url)
        if not data or not mime:
            raise HTTPException(status_code=502, detail="Failed to fetch image or unsupported format.")

        headers = cover_cache_headers(cache_key)
        return RawResponse(content=data, media_type=mime, headers=headers)

    raise HTTPException(status_code=400, detail="Either 'key' or 'url' must be specified.")


@router.post("/cover")
async def set_cover(
    request: Request,
    target_type: str = Form(...),
    target_id: int = Form(...),
    url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user)
):
    """Upload custom cover image or download from URL with SSRF validation.

    Admin-only: catalog artwork is curated, not per-user.
    """
    custom_key = None

    if file:
        # Pre-check declared size before buffering anything into RAM.
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > MAX_UPLOAD_BYTES + 1024 * 1024:  # +multipart overhead margin
                    raise HTTPException(status_code=413, detail="Uploaded file is too large.")
            except ValueError:
                pass
        # Bounded read: never buffer more than the cap, even if the client lies.
        try:
            content = await file.read(MAX_UPLOAD_BYTES)
        finally:
            # Release the spool file descriptor deterministically instead of
            # relying on garbage collection (matters for on-disk spooling).
            await file.close()
        if len(content) >= MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Uploaded file is too large.")
        custom_key = await run_in_threadpool(cover_cache.save_custom_cover, content)
        if not custom_key:
            raise HTTPException(status_code=400, detail="Invalid image file or unsupported format.")
    elif url:
        if not await is_safe_host(url):
            raise HTTPException(status_code=400, detail="Restricted or invalid image URL.")
        # A hand-picked cover must be the bytes for THIS url. fetch_and_cache
        # short-circuits on the URL-keyed cache (right for enrichment), which
        # could hand back a picture stored for a different purpose.
        data, _ = await cover_cache.fetch_fresh(url)
        if not data:
            raise HTTPException(status_code=502, detail="Failed to download image from provided URL.")
        custom_key = await run_in_threadpool(cover_cache.save_custom_cover, data)
    else:
        raise HTTPException(status_code=400, detail="Provide either an image file or a valid URL.")

    library_service.set_custom_cover(db, target_type, target_id, custom_key)
    return {"status": "ok", "custom_key": custom_key}


@router.delete("/cover")
async def reset_cover(
    target_type: str = Query(...),
    target_id: int = Query(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user)
):
    """Reset custom cover to default auto-enriched image. Admin-only."""
    library_service.set_custom_cover(db, target_type, target_id, None)
    return {"status": "ok", "message": "Cover reset to default."}
