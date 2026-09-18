"""
T-Music Playlist Schemas
"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class PlaylistTrack(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_id: int
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None
    position: int
    stream_url: str


class PlaylistSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    track_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PlaylistDetail(PlaylistSummary):
    tracks: List[PlaylistTrack] = Field(default_factory=list)


class PlaylistItemAdd(BaseModel):
    file_id: int
