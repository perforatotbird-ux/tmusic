"""
T-Music Virtual File System (VFS) Models
Represents folders, closures, audio files, and Telegram cluster accounts.
"""
from typing import Optional, List, Dict
from datetime import datetime
from sqlalchemy import (
    String, Boolean, DateTime, ForeignKey, BigInteger, Text,
    Index, JSON, Integer
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.utils.time import utcnow


class TGAccount(Base):
    """Telegram account credentials for streaming from MTProto cluster."""
    __tablename__ = "tg_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    api_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    api_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    session_string: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="active")
    last_used: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Folder(Base):
    """VFS Directory representation."""
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("folders.id"), nullable=True)
    is_trash: Mapped[bool] = mapped_column(Boolean, default=False)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False)
    color: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    custom_icon: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cached_total_size: Mapped[int] = mapped_column(BigInteger, default=0)
    cached_file_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    files: Mapped[List["VFile"]] = relationship("VFile", back_populates="folder")

    __table_args__ = (
        Index("idx_folders_parent_trash", "parent_id", "is_trash"),
    )


class FolderClosure(Base):
    """Closure table for high-speed recursive hierarchical queries."""
    __tablename__ = "folder_closures"

    ancestor_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), primary_key=True)
    descendant_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), primary_key=True)
    depth: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        Index("idx_folder_closures_ancestor", "ancestor_id"),
        Index("idx_folder_closures_descendant", "descendant_id"),
    )


class VFile(Base):
    """Virtual File representation for streamed audio tracks and metadata assets."""
    __tablename__ = "vfiles"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, index=True)
    size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    tg_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    msg_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tg_file_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    folder_id: Mapped[Optional[int]] = mapped_column(ForeignKey("folders.id"), nullable=True)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False)
    is_trash: Mapped[bool] = mapped_column(Boolean, default=False)
    pending_deletion: Mapped[bool] = mapped_column(Boolean, default=False)
    encryption_salt: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    encryption_header: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    custom_icon: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Admin-uploaded track artwork. Mirrors music_artists/music_albums so a
    # single cover pipeline (cover_cache keys + /api/library/cover) serves all
    # three catalog levels. NULL means "inherit the album/artist artwork".
    custom_image_key: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    account_map: Mapped[Optional[Dict]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    folder: Mapped[Optional["Folder"]] = relationship("Folder", back_populates="files")

    __table_args__ = (
        Index("idx_vfiles_folder_trash", "folder_id", "is_trash"),
        Index("idx_vfiles_mime_type", "mime_type"),
        Index("idx_vfiles_trash_mime", "is_trash", "mime_type"),
    )
