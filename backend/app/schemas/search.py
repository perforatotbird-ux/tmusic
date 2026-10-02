"""
T-Music Search Schemas
Typed Pydantic models for unified and category-specific search responses.
"""
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class SearchTrackItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    file_name: str
    size: Optional[int] = None
    duration: Optional[float] = None
    stream_url: str
    folder_id: Optional[int] = None
    album_id: Optional[int] = None
    album_title: Optional[str] = None
    artist_id: Optional[int] = None
    artist_name: Optional[str] = None
    cover_url: Optional[str] = None
    custom_image_url: Optional[str] = None


class SearchArtistItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    kind: str = "artist"
    image_url: Optional[str] = None
    custom_image_url: Optional[str] = None
    album_count: int = 0
    folder_id: int


class SearchAlbumItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    year: Optional[int] = None
    artist_id: int
    artist_name: Optional[str] = None
    cover_url: Optional[str] = None
    custom_image_url: Optional[str] = None
    folder_id: int


class SearchUnifiedResponse(BaseModel):
    query: str
    artists: List[SearchArtistItem]
    albums: List[SearchAlbumItem]
    tracks: List[SearchTrackItem]
    total_artists: int
    total_albums: int
    total_tracks: int


class SearchPaginatedTracks(BaseModel):
    query: str
    items: List[SearchTrackItem]
    total: int
    limit: int
    offset: int


class SearchPaginatedArtists(BaseModel):
    query: str
    items: List[SearchArtistItem]
    total: int
    limit: int
    offset: int


class SearchPaginatedAlbums(BaseModel):
    query: str
    items: List[SearchAlbumItem]
    total: int
    limit: int
    offset: int
