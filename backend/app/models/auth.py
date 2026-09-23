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
from app.utils.time import utcnow


class AppUser(Base):
    """Application user credentials with lockout counters for brute-force prevention."""
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Session generation counter: bumped on password change so every JWT
    # issued before the change is rejected (see dependencies._authenticate_request).
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    # Fernet-encrypted Subsonic client token (used as password / md5 token auth)
    subsonic_token_enc: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Future-ready RBAC: 'admin' | 'editor' | 'user'. Username 'admin' is treated as admin.
    role: Mapped[str] = mapped_column(String(32), default="user")
    # Profile avatar: key in the custom-cover store + bumped version for cache busting.
    avatar_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    avatar_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin" or self.username == "admin"


class AppSetting(Base):
    """Key/value operational settings (admin panel, backup schedule, etc.)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RevokedToken(Base):
    """Blacklist / Revocation tracker for invalidated JWT / session tokens."""
    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    # unique=True already creates an index on jti; expires_at keeps a plain index for cleanup sweeps.
    jti: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
