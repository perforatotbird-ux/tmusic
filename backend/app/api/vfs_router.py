"""
T-Music VFS folder browsing API (Windows-style hierarchy for the UI).
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.models.auth import AppUser
from app.models.vfs import Folder, FolderClosure, VFile
from app.repositories.file_repository import FileRepository
from app.schemas.stream import TrackResponse

router = APIRouter(prefix="/api/vfs", tags=["vfs"])


def _folder_payload(db: Session, folder: Folder, parent_id: Optional[int]) -> dict:
    # Count immediate subfolders + audio files (non-recursive fast counts)
    subfolders = db.scalar(
        select(func.count(Folder.id)).where(
            Folder.parent_id == folder.id,
            Folder.is_trash == False,  # noqa: E712
        )
    ) or 0
    tracks = db.scalar(
        select(func.count(VFile.id)).where(
            VFile.folder_id == folder.id,
            VFile.is_trash == False,  # noqa: E712
            VFile.mime_type.like("audio/%"),
        )
    ) or 0
    # Recursive audio count via closure (includes this folder as depth 0)
    recursive_tracks = db.scalar(
        select(func.count(func.distinct(VFile.id)))
        .join(FolderClosure, VFile.folder_id == FolderClosure.descendant_id)
        .where(
            FolderClosure.ancestor_id == folder.id,
            VFile.is_trash == False,  # noqa: E712
            VFile.mime_type.like("audio/%"),
        )
    ) or 0
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": parent_id if parent_id is not None else folder.parent_id,
        "is_trash": bool(folder.is_trash),
        "is_starred": bool(folder.is_starred),
        "color": folder.color,
        "subfolder_count": int(subfolders),
        "track_count": int(tracks),
        "recursive_track_count": int(recursive_tracks),
        "created_at": folder.created_at.isoformat() if folder.created_at else None,
        "updated_at": folder.updated_at.isoformat() if folder.updated_at else None,
    }


@router.get("/roots")
async def list_roots(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Top-level folders (parent_id is NULL), trash excluded."""
    folders = list(
        db.scalars(
            select(Folder)
            .where(Folder.parent_id.is_(None), Folder.is_trash == False)  # noqa: E712
            .order_by(Folder.name)
        ).all()
    )
    return [_folder_payload(db, f, None) for f in folders]


@router.get("/folders")
async def list_folders(
    parent_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Child folders of parent_id; omit parent_id for roots."""
    stmt = select(Folder).where(Folder.is_trash == False)  # noqa: E712
    if parent_id is None:
        stmt = stmt.where(Folder.parent_id.is_(None))
    else:
        stmt = stmt.where(Folder.parent_id == parent_id)
    folders = list(db.scalars(stmt.order_by(Folder.name)).all())
    return [_folder_payload(db, f, parent_id) for f in folders]


@router.get("/folders/{folder_id}")
async def get_folder(
    folder_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    folder = db.scalar(select(Folder).where(Folder.id == folder_id))
    if not folder or folder.is_trash:
        raise HTTPException(status_code=404, detail="Folder not found.")
    payload = _folder_payload(db, folder, folder.parent_id)
    # breadcrumb
    crumbs = []
    cur = folder
    guard = 0
    while cur and guard < 32:
        crumbs.append({"id": cur.id, "name": cur.name})
        if cur.parent_id is None:
            break
        cur = db.scalar(select(Folder).where(Folder.id == cur.parent_id))
        guard += 1
    payload["breadcrumb"] = list(reversed(crumbs))
    return payload


@router.get("/folders/{folder_id}/children")
async def folder_children(
    folder_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    folder = db.scalar(select(Folder).where(Folder.id == folder_id))
    if not folder or folder.is_trash:
        raise HTTPException(status_code=404, detail="Folder not found.")
    subfolders = list(
        db.scalars(
            select(Folder)
            .where(Folder.parent_id == folder_id, Folder.is_trash == False)  # noqa: E712
            .order_by(Folder.name)
        ).all()
    )
    tracks_vf = FileRepository.list_tracks_in_folder(db, folder_id, limit=500)
    from app.services.stream_service import stream_service

    tracks = []
    for vf in tracks_vf:
        plain = stream_service.get_track_size(vf)
        tracks.append(
            TrackResponse(
                id=vf.id,
                name=vf.name,
                size=plain,
                mime_type=vf.mime_type or "audio/mpeg",
                folder_id=vf.folder_id,
                stream_url=f"/api/stream/{vf.id}",
                created_at=vf.created_at,
            )
        )
    return {
        "folder": _folder_payload(db, folder, folder.parent_id),
        "subfolders": [_folder_payload(db, f, folder_id) for f in subfolders],
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
    folder = db.scalar(select(Folder).where(Folder.id == folder_id))
    if not folder or folder.is_trash:
        raise HTTPException(status_code=404, detail="Folder not found.")
    if recursive:
        vfiles = FileRepository.list_tracks_recursive(db, folder_id, limit=2000)
    else:
        vfiles = FileRepository.list_tracks_in_folder(db, folder_id, limit=2000)
    from app.services.stream_service import stream_service

    out = []
    for vf in vfiles:
        plain = stream_service.get_track_size(vf)
        out.append(
            TrackResponse(
                id=vf.id,
                name=vf.name,
                size=plain,
                mime_type=vf.mime_type or "audio/mpeg",
                folder_id=vf.folder_id,
                stream_url=f"/api/stream/{vf.id}",
                created_at=vf.created_at,
            )
        )
    return out
