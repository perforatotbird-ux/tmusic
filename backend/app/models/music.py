"""
T-Music Catalog Models
Represents Music Artists, Albums, Compilations, Box-sets, and Cover Art bindings.
"""
from typing import Optional, List
from datetime import datetime
from sqlalchemy import (
    String, Boolean, DateTime, ForeignKey, Text,
    Index, Integer
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.utils.time import utcnow


class MusicArtist(Base):
    """Music Artist representation mapped to a VFS Folder."""
    __tablename__ = "music_artists"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String, default="artist", index=True)  # artist | compilation | standalone_album | genre_collection
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("music_artists.id", ondelete="SET NULL"), nullable=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String)
    search_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mbid: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    wikipedia_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    custom_image_key: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    enriched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    albums: Mapped[List["MusicAlbum"]] = relationship(
        "MusicAlbum", back_populates="artist", cascade="all, delete-orphan"
    )
    # Self-referencing catalog hierarchy: collections/compilations can nest
    # artists, other collections, etc. Deletion detaches children (SET NULL),
    # never cascades into them.
    parent: Mapped[Optional["MusicArtist"]] = relationship(
        "MusicArtist", remote_side=[id], backref="children"
    )

    __table_args__ = (
        Index("idx_music_artists_kind", "kind"),
        Index("idx_music_artists_display_name", "display_name"),
        Index("idx_music_artists_search_name", "search_name"),
        Index("idx_music_artists_parent", "parent_id"),
    )


class MusicAlbum(Base):
    """Music Album / Release representation."""
    __tablename__ = "music_albums"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    folder_id: Mapped[int] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"), unique=True, index=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("music_artists.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String)
    year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mbid: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cover_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    custom_image_key: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    enriched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    artist: Mapped["MusicArtist"] = relationship("MusicArtist", back_populates="albums")

    __table_args__ = (
        Index("idx_music_albums_title", "title"),
        Index("idx_music_albums_artist_year", "artist_id", "year"),
    )
