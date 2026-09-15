"""
T-Music Authentication Schemas
"""
from typing import Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)


class PinLoginRequest(BaseModel):
    pin: str = Field(..., min_length=4, max_length=12, pattern=r"^\d+$")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserResponse(BaseModel):
    id: int
    username: str
    is_active: bool


class ChangePinRequest(BaseModel):
    current_password: str
    new_pin: str = Field(..., min_length=4, max_length=12, pattern=r"^\d+$")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)
