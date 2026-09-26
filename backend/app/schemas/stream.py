"""
T-Music Streaming Schemas
"""
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class TrackResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    size: Optional[int] = None
    mime_type: Optional[str] = None
    folder_id: Optional[int] = None
    stream_url: Optional[str] = None
    created_at: Optional[datetime] = None
    # Real duration learned from playback history (None until the track has
    # been streamed at least once — VFS stores no probe metadata).
    duration: Optional[float] = None


class StreamTicketResponse(BaseModel):
    ticket: str
    stream_url: str
    expires_in_seconds: int = 3600
