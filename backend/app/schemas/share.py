"""
T-Music Share Schemas
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class ShareCreate(BaseModel):
    file_id: int
    expires_in_hours: Optional[int] = Field(default=None, ge=1, le=24 * 365)


class ShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    token: str
    file_id: int
    track_name: Optional[str] = None
    share_url: str
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
