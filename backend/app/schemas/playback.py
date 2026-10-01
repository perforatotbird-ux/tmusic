"""
T-Music Playback Schemas
"""
from typing import List, Optional
from datetime import datetime
import math
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

MAX_HISTORY_DURATION_SEC = 24 * 3600


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
    duration_played_sec: FiniteFloat = Field(default=0.0, ge=0, le=MAX_HISTORY_DURATION_SEC)
    track_duration_sec: Optional[FiniteFloat] = Field(default=None, ge=0, le=MAX_HISTORY_DURATION_SEC)
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

    @field_validator("duration_played_sec", "track_duration_sec", mode="before")
    @classmethod
    def sanitize_legacy_duration(cls, value, info):
        # Existing invalid rows must not produce null for a required number or
        # nonfinite JSON. Normalize on read only; never rewrite history here.
        try:
            number = float(value)
            if math.isfinite(number) and 0 <= number <= MAX_HISTORY_DURATION_SEC:
                return number
        except (TypeError, ValueError, OverflowError):
            pass
        return 0.0 if info.field_name == "duration_played_sec" else None

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
    duration: Optional[float] = None


class FavoriteToggleResponse(BaseModel):
    file_id: int
    is_favorite: bool


class FavoriteItem(BaseModel):
    file_id: int
    file_name: Optional[str] = None
    size: Optional[int] = None
    mime_type: Optional[str] = None
    folder_id: Optional[int] = None
    stream_url: str
    created_at: Optional[datetime] = None
    duration: Optional[float] = None


class FavoriteList(BaseModel):
    items: List[FavoriteItem]
    total: int


class LyricsResponse(BaseModel):
    found: bool = False
    source: Optional[str] = None
    plain: Optional[str] = None
    synced: Optional[str] = None
    artist: Optional[str] = None
    track: Optional[str] = None
    album: Optional[str] = None
    duration_sec: Optional[float] = None
