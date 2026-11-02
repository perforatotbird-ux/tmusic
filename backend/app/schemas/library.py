"""
T-Music Library Schemas
"""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class MusicAlbumCard(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folder_id: int
    artist_id: int
    title: str
    year: Optional[int] = None
    mbid: Optional[str] = None
    cover_url: Optional[str] = None
    custom_image_url: Optional[str] = None
    confidence: int = 0
    is_tracks: bool = False
    enriched_at: Optional[datetime] = None


class MusicArtistCard(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folder_id: int
    kind: str
    display_name: str
    image_url: Optional[str] = None
    custom_image_url: Optional[str] = None
    confidence: int = 0
    album_count: int = 0
    parent_id: Optional[int] = None
    parent_name: Optional[str] = None
    children_count: int = 0
    enriched_at: Optional[datetime] = None


class BreadcrumbItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    kind: str


class MusicArtistDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    folder_id: int
    kind: str
    display_name: str
    search_name: Optional[str] = None
    mbid: Optional[str] = None
    image_url: Optional[str] = None
    custom_image_url: Optional[str] = None
    bio: Optional[str] = None
    wikipedia_url: Optional[str] = None
    confidence: int = 0
    locked: bool = False
    parent_id: Optional[int] = None
    parent_name: Optional[str] = None
    enriched_at: Optional[datetime] = None
    albums: List[MusicAlbumCard] = []
    children: List[MusicArtistCard] = []
    breadcrumbs: List[BreadcrumbItem] = []


class LibraryStats(BaseModel):
    artists_count: int
    albums_count: int
    tracks_count: int
    total_size_bytes: int


class SetCoverRequest(BaseModel):
    target_type: str  # 'artist' or 'album'
    target_id: int
    image_url: Optional[str] = None  # URL to fetch or None if resetting


class UpdateArtistRequest(BaseModel):
    display_name: Optional[str] = None
    search_name: Optional[str] = None
    bio: Optional[str] = None
    locked: Optional[bool] = None
