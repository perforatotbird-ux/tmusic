"""
T-Music Metadata API (Last.fm / Discogs readiness + lookups).
No secrets in responses.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.auth import AppUser
from app.models.music import MusicArtist
from app.services import lastfm_service, lyrics_service
from app.schemas.playback import LyricsResponse

router = APIRouter(prefix="/api/metadata", tags=["metadata"])


@router.get("/status")
async def metadata_status(user: AppUser = Depends(get_current_user)):
    return {
        "lastfm": {
            "enabled": settings.LASTFM_ENABLED,
            "api_key": bool(settings.LASTFM_API_KEY),
            "shared_secret": bool(settings.lastfm_secret),
            "session_key": bool(settings.LASTFM_SESSION_KEY),
            "metadata_ready": settings.lastfm_metadata_ready,
            "scrobble_ready": settings.lastfm_scrobble_ready,
        },
        "discogs": {
            "consumer_key": bool(settings.DISCOGS_CONSUMER_KEY),
            "consumer_secret": bool(settings.DISCOGS_CONSUMER_SECRET),
            "ready": settings.discogs_ready,
        },
        "lyrics": {
            "enabled": lyrics_service.lyrics_enabled(),
            "source": "lrclib",
        },
    }


@router.get("/lyrics", response_model=LyricsResponse)
async def lyrics_lookup(
    artist: Optional[str] = Query(None, max_length=200),
    track: Optional[str] = Query(None, max_length=300),
    album: Optional[str] = Query(None, max_length=200),
    duration: Optional[float] = Query(None, ge=0, le=6 * 3600),
    file_name: Optional[str] = Query(None, max_length=500),
    user: AppUser = Depends(get_current_user),
):
    """Fetch plain + synced (LRC) lyrics from LRCLIB. found=false when missing."""
    if not lyrics_service.lyrics_enabled():
        raise HTTPException(status_code=503, detail="Lyrics provider is disabled.")
    data = lyrics_service.get_lyrics(
        artist=artist,
        track=track,
        album=album,
        duration_sec=duration,
        file_name=file_name,
    )
    if not data:
        return LyricsResponse(found=False, source="lrclib")
    return LyricsResponse(**data)


@router.get("/artist")
async def artist_lookup(
    name: str = Query(..., min_length=1, max_length=200),
    user: AppUser = Depends(get_current_user),
):
    if not lastfm_service.metadata_ready():
        raise HTTPException(status_code=503, detail="Last.fm metadata is not configured.")
    data = lastfm_service.get_artist_info(name)
    if not data:
        raise HTTPException(status_code=404, detail="Artist not found on Last.fm.")
    return data


@router.get("/track")
async def track_lookup(
    artist: str = Query(..., min_length=1, max_length=200),
    track: str = Query(..., min_length=1, max_length=300),
    album: Optional[str] = Query(None, max_length=200),
    user: AppUser = Depends(get_current_user),
):
    if not lastfm_service.metadata_ready():
        raise HTTPException(status_code=503, detail="Last.fm metadata is not configured.")
    data = lastfm_service.get_track_info(artist, track, album)
    if not data:
        raise HTTPException(status_code=404, detail="Track not found on Last.fm.")
    return data


@router.post("/artists/{artist_id}/enrich")
async def enrich_artist(
    artist_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Pull Last.fm bio/tags into music_artists when missing."""
    if not lastfm_service.metadata_ready():
        raise HTTPException(status_code=503, detail="Last.fm metadata is not configured.")
    artist = db.scalar(select(MusicArtist).where(MusicArtist.id == artist_id))
    if not artist:
        raise HTTPException(status_code=404, detail="Artist not found.")
    info = lastfm_service.get_artist_info(artist.display_name)
    if not info:
        raise HTTPException(status_code=404, detail="Artist not found on Last.fm.")
    changed = False
    if not artist.bio and info.get("summary"):
        artist.bio = info["summary"]
        changed = True
    if not artist.wikipedia_url and info.get("url"):
        # Last.fm artist page is not Wikipedia, but useful reference link
        pass
    if not artist.image_url and info.get("image_url"):
        artist.image_url = info["image_url"]
        changed = True
    if info.get("mbid") and not artist.mbid:
        artist.mbid = info["mbid"]
        changed = True
    if changed:
        db.commit()
        db.refresh(artist)
    return {
        "id": artist.id,
        "display_name": artist.display_name,
        "bio": artist.bio,
        "image_url": artist.image_url,
        "mbid": artist.mbid,
        "similar": info.get("similar") or [],
        "tags": info.get("tags") or [],
        "updated": changed,
    }
