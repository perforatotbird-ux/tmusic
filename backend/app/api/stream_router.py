"""
T-Music Audio Streaming API Router
Delivers high-performance audio streams with HTTP Range requests (RFC 7233)
and transparent on-the-fly AES-256-GCM block decryption.
"""
import urllib.parse
from datetime import timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token
from app.api.dependencies import get_stream_user, get_current_user
from app.models.auth import AppUser
from app.repositories.file_repository import FileRepository
from app.schemas.stream import TrackResponse
from app.services.stream_service import stream_service

router = APIRouter(prefix="/api/stream", tags=["stream"])

# Lifetime of single-purpose ?token= stream tokens. Short on purpose: URLs leak
# into proxy logs, browser history, and Referer headers, so a 12h session JWT
# must never travel in the query string.
STREAM_TOKEN_TTL = timedelta(minutes=10)


@router.post("/token")
async def mint_stream_token(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Mint a short-lived scope="stream" token for native players that cannot
    send cookies or Authorization headers (HTML5 <audio> on some platforms,
    external players). Valid 10 minutes, usable ONLY as ?token=."""
    token_str, _, expire_dt = create_access_token(
        subject=user.username,
        claims={
            "uid": user.id,
            "tv": user.token_version or 0,
            "scope": "stream",
        },
        expires_delta=STREAM_TOKEN_TTL,
    )
    return {"stream_token": token_str, "expires_in": int(STREAM_TOKEN_TTL.total_seconds())}


@router.get("/tracks/{folder_id}", response_model=List[TrackResponse])
async def get_folder_tracks(
    folder_id: int,
    recursive: bool = Query(True, description="Whether to include tracks in subfolders (multi-disc)"),
    limit: Optional[int] = Query(None, ge=1, le=1000, description="Optional page size; omit for full album list"),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user)
):
    """Retrieve all audio tracks belonging to an album or folder with natural sorting and plaintext sizes."""
    if recursive:
        vfiles = FileRepository.list_tracks_recursive(db, folder_id, limit=limit, offset=offset)
    else:
        vfiles = FileRepository.list_tracks_in_folder(db, folder_id, limit=limit, offset=offset)

    tracks = []
    for vf in vfiles:
        plain_size = stream_service.get_track_size(vf)
        tracks.append(TrackResponse(
            id=vf.id,
            name=vf.name,
            size=plain_size,
            mime_type=vf.mime_type or "audio/mpeg",
            folder_id=vf.folder_id,
            stream_url=f"/api/stream/{vf.id}",
            created_at=vf.created_at
        ))
    return tracks


@router.get("/{file_id}")
async def stream_audio_file(
    file_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_stream_user)
):
    """
    Stream audio track chunk-by-chunk directly from Telegram MTProto or RAM cache.
    Full support for HTTP 206 Partial Content Range scrubbing on both plain and encrypted files.
    """
    track = stream_service.get_track_or_404(db, file_id)
    total_size = stream_service.get_track_size(track)
    if total_size <= 0:
        raise HTTPException(status_code=400, detail="Audio file has invalid size.")

    range_header = request.headers.get("Range")
    start, end, content_length = stream_service.parse_range_header(range_header, total_size)

    media_type = track.mime_type or "audio/mpeg"
    encoded_name = urllib.parse.quote(track.name)

    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{total_size}",
        "Content-Length": str(content_length),
        "Cache-Control": "private, max-age=3600"
    }

    status_code = 206 if range_header else 200

    stream_gen = stream_service.get_stream_generator(track, start, content_length)

    return StreamingResponse(
        stream_gen,
        status_code=status_code,
        media_type=media_type,
        headers=headers
    )
