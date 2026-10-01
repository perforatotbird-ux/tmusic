"""
T-Music Share Schemas
"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class ShareCreate(BaseModel):
    file_id: int
    # Omitted → server default TTL (settings.SHARE_DEFAULT_TTL_HOURS, 30 days).
    # Explicit 0 → permanent (legacy behaviour, opt-in). Max is 1 year.
    expires_in_hours: Optional[int] = Field(default=None, ge=0, le=24 * 365)


class ShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    token: str
    file_id: int
    track_name: Optional[str] = None
    share_url: str
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
