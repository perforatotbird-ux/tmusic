"""
T-Music Playback Schemas
"""
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class PositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: int
    position_sec: float = 0.0
    duration_sec: Optional[float] = None
    updated_at: Optional[datetime] = None


class PositionUpdate(BaseModel):
    position_sec: float = Field(ge=0, le=24 * 3600)
    duration_sec: Optional[float] = Field(default=None, ge=0, le=24 * 3600)


class HistoryCreate(BaseModel):
    file_id: int
    duration_played_sec: float = Field(default=0.0, ge=0)
    track_duration_sec: Optional[float] = Field(default=None, ge=0)
    completed: bool = False
    artist: Optional[str] = Field(default=None, max_length=200)
    track: Optional[str] = Field(default=None, max_length=300)
    album: Optional[str] = Field(default=None, max_length=200)


class HistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_id: int
    file_name: Optional[str] = None
    started_at: datetime
    duration_played_sec: float = 0.0
    track_duration_sec: Optional[float] = None
    completed: bool = False


class HistoryList(BaseModel):
    items: List[HistoryItem]
    total: int


class TrackSearchItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    size: Optional[int] = None
    mime_type: Optional[str] = None
    folder_id: Optional[int] = None
    stream_url: str
