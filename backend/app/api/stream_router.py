"""
T-Music Audio Streaming API Router
Delivers high-performance audio streams with HTTP Range requests (RFC 7233)
and transparent on-the-fly AES-256-GCM block decryption.
Also includes SSE-based transcoding notifications and transcoded MP3 delivery.
"""
import urllib.parse
import asyncio
import json
from datetime import timedelta
from typing import Optional, List, Annotated
from pydantic import Field
from sqlalchemy import select
from app.core.rate_limiter import limiter
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token
from app.api.dependencies import get_stream_user, get_current_user
from app.models.auth import AppUser
from app.models.vfs import VFile
from app.repositories.file_repository import FileRepository
from app.schemas.stream import TrackResponse
from app.services.stream_service import stream_service
from app.services.transcoding_service import transcoding_service, TranscodeStatus
from app.utils.ffmpeg_transcoder import needs_transcoding, get_cached_transcode_path

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


# ─── Transcoding SSE (Server-Sent Events) ──────────────────────────────────────

@router.get("/transcode-events")
async def transcode_events(
    request: Request,
    user: AppUser = Depends(get_current_user),
):
    """SSE stream for real-time transcoding notifications.
    Frontend connects with: const evtSource = new EventSource('/api/stream/transcode-events');"""
    try:
        client_queue = transcoding_service.subscribe(user.id)
    except ValueError:
        raise HTTPException(status_code=429, detail="Too many SSE connections.")

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(client_queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    # Heartbeat every 30s to keep connection alive
                    yield "data: {\"event\":\"ping\"}\n\n"
                    continue

                if event is None:
                    break

                data = json.dumps(event)
                yield f"data: {data}\n\n"
                client_queue.task_done()
        except Exception:
            pass
        finally:
            transcoding_service.unsubscribe(user.id, client_queue)

    # Set headers for SSE
    response = StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )
    return response


# ─── Transcoding status and control endpoints ────────────────────────────────

@router.get("/{file_id}/transcode-status")
async def get_transcode_status(
    file_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Check transcoding status for a track. Returns current state + cached path if ready."""
    track = stream_service.get_track_or_404(db, file_id)

    if not needs_transcoding(track.name, track.mime_type):
        return {"status": "not_needed", "file_id": file_id, "mime_type": track.mime_type}

    status = transcoding_service.get_status(file_id)
    result = transcoding_service.get_result(file_id)

    response = {
        "file_id": file_id,
        "file_name": track.name,
        "status": status.value,
        "mime_type": track.mime_type,
    }
    if result and result.get("cached_path"):
        response.update({
            "mp3_size": result.get("mp3_size"),
            "duration": result.get("duration"),
        })
    return response


@router.post("/{file_id}/transcode")
async def trigger_transcode(
    file_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Trigger immediate transcoding of a track (priority: playing)."""
    track = stream_service.get_track_or_404(db, file_id)

    if not needs_transcoding(track.name, track.mime_type):
        return {"status": "not_needed", "file_id": file_id, "message": "Track doesn't need transcoding"}

    status = transcoding_service.request_transcode(
        track,
        priority=0,
        requester_user_id=user.id,
    )
    return {"file_id": file_id, "status": status.value, "message": "Transcoding started or already queued"}


@router.post("/transcode/prefetch")
async def prefetch_tracks(
    track_ids: Annotated[List[Annotated[int, Field(gt=0)]], Field(min_length=1, max_length=50)],
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Enqueue multiple tracks for background prefetch transcoding."""
    from app.utils.ffmpeg_transcoder import needs_transcoding

    if limiter.is_rate_limited(f"transcode:{user.id}", 10, 60):
        raise HTTPException(status_code=429, detail="Too many transcoding requests.")

    enqueued = 0
    already_ready = 0
    not_needed = 0

    tracks = db.scalars(select(VFile).where(
        VFile.id.in_(set(track_ids)), VFile.is_trash.is_(False),
        VFile.mime_type.like("audio/%"),
    )).all()
    for track in tracks:
        file_id = track.id
        if not needs_transcoding(track.name, track.mime_type):
            not_needed += 1
            continue
        status = transcoding_service.get_status(file_id)
        if status == TranscodeStatus.READY:
            already_ready += 1
            continue
        transcoding_service.request_transcode(
            track,
            priority=1,
            requester_user_id=user.id,
        )
        enqueued += 1

    return {
        "enqueued": enqueued,
        "already_ready": already_ready,
        "not_needed": not_needed,
        "total_requested": len(track_ids),
    }


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

    return stream_service.build_track_responses(db, vfiles)


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
    If a transcoded MP3 is cached on disk, serves that instead.
    """
    track = stream_service.get_track_or_404(db, file_id)

    # Check if this track needs transcoding and has a cached MP3
    needs_transcode = needs_transcoding(track.name, track.mime_type)
    transcoded_path = None

    if needs_transcode:
        result = transcoding_service.get_result(file_id)
        if result and result.get("cached_path"):
            transcoded_path = result["cached_path"]

    if transcoded_path:
        # Serve the transcoded MP3 from disk cache
        from pathlib import Path

        cached_size = Path(transcoded_path).stat().st_size
        range_header = request.headers.get("Range")
        start, end, content_length = stream_service.parse_range_header(range_header, cached_size)

        media_type = "audio/mpeg"
        encoded_name = urllib.parse.quote(track.name)

        headers = {
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Cache-Control": "private, max-age=3600",
        }

        valid_range = bool(range_header and range_header.startswith("bytes=") and "," not in range_header)
        status_code = 206 if valid_range else 200
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{cached_size}"

        def file_chunk_generator():
            with open(transcoded_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = f.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    yield chunk
                    remaining -= len(chunk)

        return StreamingResponse(
            file_chunk_generator(),
            status_code=status_code,
            media_type=media_type,
            headers=headers
        )

    # Fall back to original streaming (from Telegram / RAM cache)
    total_size = stream_service.get_track_size(track)
    if total_size <= 0:
        raise HTTPException(status_code=400, detail="Audio file has invalid size.")

    # If this track needs transcoding but isn't cached yet, trigger background encoding.
    # Byte-range prefetch requests (the player buffering upcoming tracks) are
    # marked with X-Prefetch and must NOT enqueue anything — otherwise every
    # queue start mass-transcodes the whole upcoming window at PLAYING priority.
    # Only a real playback request may trigger this.
    if needs_transcode and request.headers.get("X-Prefetch") != "1":
        transcoding_service.request_transcode(
            track,
            priority=0,
            requester_user_id=getattr(user, "id", None),
        )

    range_header = request.headers.get("Range")
    start, end, content_length = stream_service.parse_range_header(range_header, total_size)

    media_type = track.mime_type or "audio/mpeg"
    encoded_name = urllib.parse.quote(track.name)

    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Cache-Control": "private, max-age=3600"
    }

    valid_range = bool(range_header and range_header.startswith("bytes=") and "," not in range_header)
    status_code = 206 if valid_range else 200
    if status_code == 206:
        # Content-Range MUST only be sent with 206 (RFC 7233). Sending it
        # on a 200 breaks duration detection in Chrome — duration stays
        # NaN/Infinity, 'ended' never fires, playlist auto-advance stalls.
        headers["Content-Range"] = f"bytes {start}-{end}/{total_size}"

    stream_gen = stream_service.get_stream_generator(track, start, content_length)

    return StreamingResponse(
        stream_gen,
        status_code=status_code,
        media_type=media_type,
        headers=headers
    )
