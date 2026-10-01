"""
T-Music Authentication Schemas
"""
from typing import Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    password_change_required: bool = False


class UserResponse(BaseModel):
    id: int
    username: str
    is_active: bool
    role: str = "user"
    avatar_url: Optional[str] = None
    password_change_required: bool = False


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=1024)
    new_password: str = Field(..., min_length=8, max_length=128)
