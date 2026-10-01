"""
T-Music Playlist Schemas
"""
from datetime import datetime
from typing import Annotated, List, Optional
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class PlaylistName(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        return value.strip() if isinstance(value, str) else value

class PlaylistCreate(PlaylistName):
    initial_file_id: Optional[Annotated[StrictInt, Field(gt=0)]] = None


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


class PlaylistReorder(BaseModel):
    item_ids: List[int] = Field(min_length=1, max_length=5000)


class PlaylistMirror(PlaylistName):
    file_ids: List[Annotated[StrictInt, Field(gt=0)]] = Field(max_length=5000)

    @field_validator("file_ids")
    @classmethod
    def unique_files(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("Duplicate file IDs are not allowed")
        return value
