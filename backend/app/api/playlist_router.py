"""
T-Music Playlist API
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.auth import AppUser
from app.models.vfs import VFile
from app.schemas.playlist import (
    PlaylistCreate,
    PlaylistDetail,
    PlaylistItemAdd,
    PlaylistReorder,
    PlaylistSummary,
    PlaylistTrack,
)
from app.services.playlist_service import playlist_service
from sqlalchemy import select

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def _tracks_payload(rows) -> List[PlaylistTrack]:
    out: List[PlaylistTrack] = []
    for item, vf in rows:
        out.append(
            PlaylistTrack(
                id=item.id,
                file_id=item.file_id,
                file_name=vf.name if vf else None,
                mime_type=vf.mime_type if vf else None,
                size=vf.size if vf else None,
                position=item.position,
                stream_url=f"/api/stream/{item.file_id}",
            )
        )
    return out


@router.get("", response_model=List[PlaylistSummary])
async def list_playlists(db: Session = Depends(get_db), user: AppUser = Depends(get_current_user)):
    rows = playlist_service.list_playlists(db, user.id)
    return [
        PlaylistSummary(
            id=pl.id,
            name=pl.name,
            track_count=int(cnt or 0),
            created_at=pl.created_at,
            updated_at=pl.updated_at,
        )
        for pl, cnt in rows
    ]


@router.post("", response_model=PlaylistSummary)
async def create_playlist(
    body: PlaylistCreate,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    pl = playlist_service.create_playlist(db, user.id, body.name)
    return PlaylistSummary(
        id=pl.id,
        name=pl.name,
        track_count=0,
        created_at=pl.created_at,
        updated_at=pl.updated_at,
    )


@router.get("/{playlist_id}", response_model=PlaylistDetail)
async def get_playlist(
    playlist_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    pl = playlist_service.get_playlist(db, user.id, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found.")
    rows = playlist_service.playlist_tracks(db, playlist_id)
    return PlaylistDetail(
        id=pl.id,
        name=pl.name,
        track_count=len(rows),
        created_at=pl.created_at,
        updated_at=pl.updated_at,
        tracks=_tracks_payload(rows),
    )


@router.delete("/{playlist_id}")
async def delete_playlist(
    playlist_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    ok = playlist_service.delete_playlist(db, user.id, playlist_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Playlist not found.")
    return {"status": "ok"}


@router.post("/{playlist_id}/items", response_model=PlaylistDetail)
async def add_item(
    playlist_id: int,
    body: PlaylistItemAdd,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    vf = db.scalar(select(VFile).where(VFile.id == body.file_id, VFile.is_trash == False))  # noqa: E712
    if not vf:
        raise HTTPException(status_code=404, detail="Track not found.")
    item = playlist_service.add_track(db, user.id, playlist_id, body.file_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Playlist not found.")
    pl = playlist_service.get_playlist(db, user.id, playlist_id)
    rows = playlist_service.playlist_tracks(db, playlist_id)
    return PlaylistDetail(
        id=pl.id,
        name=pl.name,
        track_count=len(rows),
        created_at=pl.created_at,
        updated_at=pl.updated_at,
        tracks=_tracks_payload(rows),
    )


@router.delete("/{playlist_id}/items/{item_id}")
async def remove_item(
    playlist_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    ok = playlist_service.remove_track(db, user.id, playlist_id, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Playlist item not found.")
    return {"status": "ok"}


@router.put("/{playlist_id}/items/order", response_model=PlaylistDetail)
async def reorder_items(
    playlist_id: int,
    body: PlaylistReorder,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Reorder all tracks: body.item_ids is the full new order of item ids."""
    pl = playlist_service.get_playlist(db, user.id, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Playlist not found.")
    ok = playlist_service.reorder_items(db, user.id, playlist_id, body.item_ids)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Invalid item_ids: must be a permutation of this playlist's items.",
        )
    rows = playlist_service.playlist_tracks(db, playlist_id)
    return PlaylistDetail(
        id=pl.id,
        name=pl.name,
        track_count=len(rows),
        created_at=pl.created_at,
        updated_at=pl.updated_at,
        tracks=_tracks_payload(rows),
    )
