"""
T-Music Authentication & Authorization Models
Designed for Internet-facing security with brute-force defense and session lifecycle.
"""
from typing import Optional
from datetime import datetime
from sqlalchemy import (
    String, Boolean, DateTime, Integer
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppUser(Base):
    """Application user credentials with lockout counters for brute-force prevention."""
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    pin_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Session generation counter: bumped on password/PIN change so every JWT
    # issued before the change is rejected (see dependencies._authenticate_request).
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RevokedToken(Base):
    """Blacklist / Revocation tracker for invalidated JWT / session tokens."""
    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique=True already creates an index on jti; expires_at keeps a plain index for cleanup sweeps.
    jti: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
