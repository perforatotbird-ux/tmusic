"""
T-Music VFS folder browsing API (Windows-style hierarchy for the UI).

Thin controller: routing + validation only; folder queries live in VfsService.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.auth import AppUser
from app.schemas.stream import TrackResponse
from app.services.stream_service import stream_service
from app.services.transcoding_service import transcoding_service
from app.services.vfs_service import vfs_service

router = APIRouter(prefix="/api/vfs", tags=["vfs"])


@router.get("/roots")
async def list_roots(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Top-level folders (parent_id is NULL), trash excluded."""
    return [vfs_service.folder_payload(db, f, None) for f in vfs_service.list_roots(db)]


@router.get("/folders")
async def list_folders(
    parent_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Child folders of parent_id; omit parent_id for roots."""
    return [vfs_service.folder_payload(db, f, parent_id) for f in vfs_service.list_children(db, parent_id)]


@router.get("/folders/{folder_id}")
async def get_folder(
    folder_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    folder = vfs_service.get_folder(db, folder_id)
    if not folder or folder.is_trash:
        raise HTTPException(status_code=404, detail="Folder not found.")
    payload = vfs_service.folder_payload(db, folder, folder.parent_id)
    payload["breadcrumb"] = vfs_service.breadcrumb(db, folder)
    return payload


@router.get("/folders/{folder_id}/children")
async def folder_children(
    folder_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    folder = vfs_service.get_folder(db, folder_id)
    if not folder or folder.is_trash:
        raise HTTPException(status_code=404, detail="Folder not found.")
    subfolders = vfs_service.list_children(db, folder_id)
    tracks_vf = stream_service.list_folder_tracks(db, folder_id, recursive=False, limit=500)
    tracks = await run_in_threadpool(stream_service.build_track_responses, db, tracks_vf)
    # 2026-10-16: открытие папки не ждёт транскодинг — нужные форматы уходят в фон.
    await run_in_threadpool(transcoding_service.prefetch_collection, tracks_vf, requester_user_id=user.id)
    return {
        "folder": vfs_service.folder_payload(db, folder, folder.parent_id),
        "subfolders": [vfs_service.folder_payload(db, f, folder_id) for f in subfolders],
        "tracks": tracks,
    }


@router.get("/folders/{folder_id}/tracks", response_model=List[TrackResponse])
async def folder_tracks_recursive(
    folder_id: int,
    recursive: bool = Query(True),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """All audio tracks under folder — used when clicking to play."""
    folder = vfs_service.get_folder(db, folder_id)
    if not folder or folder.is_trash:
        raise HTTPException(status_code=404, detail="Folder not found.")
    vfiles = stream_service.list_folder_tracks(db, folder_id, recursive=recursive, limit=2000)
    responses = await run_in_threadpool(stream_service.build_track_responses, db, vfiles)
    # 2026-10-16: клик «играть» не должен ждать первый транскод — он уже в фоне.
    await run_in_threadpool(transcoding_service.prefetch_collection, vfiles, requester_user_id=user.id)
    return responses
