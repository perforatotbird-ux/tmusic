"""
T-Music Playback API Router
Resume positions + listening history.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.auth import AppUser
from app.models.vfs import VFile
from app.schemas.playback import (
    HistoryCreate,
    HistoryItem,
    HistoryList,
    PositionResponse,
    PositionUpdate,
    TrackSearchItem,
)
from app.services.playback_service import playback_service
from app.services import lastfm_service
from app.utils.like import escape_like

router = APIRouter(prefix="/api/playback", tags=["playback"])


def _require_vfile(db: Session, file_id: int) -> VFile:
    vf = db.scalar(
        select(VFile).where(VFile.id == file_id, VFile.is_trash == False)  # noqa: E712
    )
    if not vf:
        raise HTTPException(status_code=404, detail="Track not found.")
    return vf


@router.get("/position/{file_id}", response_model=PositionResponse)
async def get_position(
    file_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    _require_vfile(db, file_id)
    row = playback_service.get_position(db, user.id, file_id)
    if not row:
        return PositionResponse(file_id=file_id, position_sec=0.0)
    return PositionResponse(
        file_id=row.file_id,
        position_sec=row.position_sec,
        duration_sec=row.duration_sec,
        updated_at=row.updated_at,
    )


@router.put("/position/{file_id}", response_model=PositionResponse)
async def put_position(
    file_id: int,
    body: PositionUpdate,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    _require_vfile(db, file_id)
    row = playback_service.upsert_position(
        db, user.id, file_id, body.position_sec, body.duration_sec
    )
    return PositionResponse(
        file_id=row.file_id,
        position_sec=row.position_sec,
        duration_sec=row.duration_sec,
        updated_at=row.updated_at,
    )


@router.get("/positions")
async def list_positions(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    rows = playback_service.list_positions(db, user.id, limit=limit)
    items = [
        {
            "file_id": pos.file_id,
            "file_name": name,
            "position_sec": pos.position_sec,
            "duration_sec": pos.duration_sec,
            "updated_at": pos.updated_at.isoformat() if pos.updated_at else None,
            "stream_url": f"/api/stream/{pos.file_id}",
        }
        for pos, name in rows
    ]
    return {"items": items}


@router.post("/history", response_model=HistoryItem)
async def create_history(
    body: HistoryCreate,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    vf = _require_vfile(db, body.file_id)
    row = playback_service.add_history(
        db,
        user.id,
        body.file_id,
        body.duration_played_sec,
        body.track_duration_sec,
        body.completed,
    )
    try:
        artist, track, album = lastfm_service.parse_track_meta(
            vf.name, body.artist, body.track, body.album
        )
        played = body.duration_played_sec or 0
        total = body.track_duration_sec or 0
        should_scrobble = body.completed or (
            total > 0 and (played >= total * 0.5 or played >= 240)
        )
        if should_scrobble:
            lastfm_service.scrobble(artist, track, album)
        else:
            lastfm_service.update_now_playing(artist, track, album)
    except Exception:
        pass
    return HistoryItem(
        id=row.id,
        file_id=row.file_id,
        file_name=vf.name,
        started_at=row.started_at,
        duration_played_sec=row.duration_played_sec,
        track_duration_sec=row.track_duration_sec,
        completed=row.completed,
    )


@router.get("/history", response_model=HistoryList)
async def list_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    rows, total = playback_service.list_history(db, user.id, limit=limit, offset=offset)
    items = [
        HistoryItem(
            id=row.id,
            file_id=row.file_id,
            file_name=name,
            started_at=row.started_at,
            duration_played_sec=row.duration_played_sec,
            track_duration_sec=row.track_duration_sec,
            completed=row.completed,
        )
        for row, name in rows
    ]
    return HistoryList(items=items, total=total)


@router.get("/tracks/search", response_model=list[TrackSearchItem])
async def search_tracks(
    search: Optional[str] = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Search audio tracks by file name (auth required)."""
    stmt = select(VFile).where(
        VFile.is_trash == False,  # noqa: E712
        VFile.mime_type.like("audio/%"),
    )
    if search and search.strip():
        pattern = f"%{escape_like(search.strip())}%"
        stmt = stmt.where(VFile.name.ilike(pattern, escape="\\"))
    stmt = stmt.order_by(VFile.name).limit(limit).offset(offset)
    files = db.scalars(stmt).all()
    return [
        TrackSearchItem(
            id=vf.id,
            name=vf.name,
            size=vf.size,
            mime_type=vf.mime_type,
            folder_id=vf.folder_id,
            stream_url=f"/api/stream/{vf.id}",
        )
        for vf in files
    ]
