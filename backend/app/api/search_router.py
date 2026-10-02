"""
T-Music Search API Router
Fast, unified and category-specific search endpoints for tracks, artists, and albums (ADR-001).
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.api.dependencies import get_current_user
from app.models.auth import AppUser
from app.schemas.search import (
    SearchUnifiedResponse,
    SearchPaginatedTracks,
    SearchPaginatedArtists,
    SearchPaginatedAlbums,
)
from app.services.search_service import SearchService

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=SearchUnifiedResponse)
async def unified_search(
    q: Optional[str] = Query(None, max_length=200, description="Search query string"),
    limit_artists: int = Query(6, ge=1, le=50, description="Max artist results"),
    limit_albums: int = Query(8, ge=1, le=50, description="Max album results"),
    limit_tracks: int = Query(15, ge=1, le=100, description="Max track results"),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """
    Search across tracks, artists, and albums in a single round-trip.
    Returns grouped ranked results with cover art, artist/album bindings, and durations.
    """
    return SearchService.search_unified(
        db,
        query=q or "",
        limit_artists=limit_artists,
        limit_albums=limit_albums,
        limit_tracks=limit_tracks,
    )


@router.get("/tracks", response_model=SearchPaginatedTracks)
async def search_tracks(
    q: Optional[str] = Query(None, max_length=200, description="Search query string"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Paginated search for audio tracks with album/artist metadata."""
    return SearchService.search_tracks_paginated(
        db,
        query=q or "",
        limit=limit,
        offset=offset,
    )


@router.get("/artists", response_model=SearchPaginatedArtists)
async def search_artists(
    q: Optional[str] = Query(None, max_length=200, description="Search query string"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Paginated search for catalog artists."""
    return SearchService.search_artists_paginated(
        db,
        query=q or "",
        limit=limit,
        offset=offset,
    )


@router.get("/albums", response_model=SearchPaginatedAlbums)
async def search_albums(
    q: Optional[str] = Query(None, max_length=200, description="Search query string"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_current_user),
):
    """Paginated search for albums with artist names."""
    return SearchService.search_albums_paginated(
        db,
        query=q or "",
        limit=limit,
        offset=offset,
    )
