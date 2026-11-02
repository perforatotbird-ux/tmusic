"""
T-Music Share API
Create/list/delete public share links; anonymous access by token.
"""
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.auth import AppUser
from app.models.share import ShareLink
from app.models.vfs import VFile
from app.schemas.share import ShareCreate, ShareResponse
from app.services.stream_service import stream_service
from app.utils.time import utcnow

# Auth-scoped CRUD under /api/shares; public token endpoints under /api/share.
router = APIRouter(prefix="/api", tags=["shares"])

TOKEN_BYTES = 18  # ~24-char URL-safe token


def _share_url(request: Request, token: str) -> str:
    # Prefer the configured public origin: a forged Host header must never
    # change the domain of a link the user copies and sends to someone else.
    base = (settings.PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
    return f"{base}/s/{token}"


def _is_expired(share: ShareLink) -> bool:
    if share.expires_at is None:
        return False
    return share.expires_at <= utcnow()


def _share_response(
    share: ShareLink,
    request: Request,
    track_name: Optional[str] = None,
) -> ShareResponse:
    return ShareResponse(
        id=share.id,
        token=share.token,
        file_id=share.file_id,
        track_name=track_name,
        share_url=_share_url(request, share.token),
        created_at=share.created_at,
        expires_at=share.expires_at,
    )


def create_share_impl(
    db: Session,
    user_id: int,
    file_id: int,
    expires_in_hours: Optional[int] = None,
) -> ShareLink:
    """Shared creation path for API handler and Subsonic createShare.

    2026-09 audit (minor 3): when no explicit expiry is given the share now
    defaults to settings.SHARE_DEFAULT_TTL_HOURS (30 days) instead of never
    expiring. Pass 0 or negative to create a permanent share explicitly via
    the API; the Subsonic path and omitted expiry always get the default TTL.
    """
    from datetime import timedelta

    ttl_hours = expires_in_hours
    if ttl_hours is None or ttl_hours <= 0:
        ttl_hours = settings.SHARE_DEFAULT_TTL_HOURS if expires_in_hours is None else 0

    share = ShareLink(
        token=secrets.token_urlsafe(TOKEN_BYTES),
        user_id=user_id,
        file_id=file_id,
    )
    if ttl_hours > 0:
        share.expires_at = utcnow().replace(microsecond=0) + timedelta(hours=ttl_hours)
    db.add(share)
    db.commit()
    db.refresh(share)
    return share


def _resolve_share(db: Session, token: str) -> ShareLink:
    share = db.scalar(select(ShareLink).where(ShareLink.token == token))
    if not share or _is_expired(share):
        raise HTTPException(status_code=404, detail="Share not found.")
    return share


async def _stream_track_response(request: Request, track) -> StreamingResponse:
    return await stream_service.build_stream_response(request, track, cache_control="public, max-age=3600")


@router.post("/shares", response_model=ShareResponse)
async def create_share(
    body: ShareCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    vf = db.scalar(select(VFile).where(VFile.id == body.file_id, VFile.is_trash == False))  # noqa: E712
    if not vf:
        raise HTTPException(status_code=404, detail="Track not found.")
    share = create_share_impl(db, user.id, body.file_id, expires_in_hours=body.expires_in_hours)
    return _share_response(share, request, track_name=vf.name)


@router.get("/shares", response_model=List[ShareResponse])
async def list_shares(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    rows = db.execute(
        select(ShareLink, VFile.name)
        .join(VFile, VFile.id == ShareLink.file_id, isouter=True)
        .where(ShareLink.user_id == user.id)
        .order_by(ShareLink.created_at.desc())
    ).all()
    return [
        _share_response(share, request, track_name=name)
        for share, name in rows
    ]


@router.delete("/shares/{share_id}")
async def delete_share(
    share_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    share = db.get(ShareLink, share_id)
    if not share or share.user_id != user.id:
        raise HTTPException(status_code=404, detail="Share not found.")
    db.delete(share)
    db.commit()
    return {"status": "ok"}


@router.get("/share/{token}", name="share_info")
async def share_info(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    share = _resolve_share(db, token)
    track = stream_service.get_track_or_404(db, share.file_id)
    return _share_response(share, request, track_name=track.name)


@router.get("/share/{token}/stream", name="share_stream")
async def share_stream(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
):
    share = _resolve_share(db, token)
    track = stream_service.snapshot_track(stream_service.get_track_or_404(db, share.file_id))
    db.close()
    return await _stream_track_response(request, track)
