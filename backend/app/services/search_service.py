"""
T-Music Search Service
Business logic for catalog search: query normalization, title cleanup,
cover URL resolution, duration enrichment, and LRU caching (ADR-001).

Cover URL contract (2026-10-03): same as music_library_service — search
cards return ``/api/library/cover?key=<cache-key>`` derived from the stored
remote URL, so display always reads from the local disk cache. No ``?url=``
is emitted for cards.
"""
import re
import time
import threading
from typing import Optional, Tuple, Dict, Any, List
from sqlalchemy.orm import Session

from app.schemas.search import (
    SearchTrackItem,
    SearchArtistItem,
    SearchAlbumItem,
    SearchUnifiedResponse,
    SearchPaginatedTracks,
    SearchPaginatedArtists,
    SearchPaginatedAlbums,
)
from app.repositories.search_repository import SearchRepository
from app.services.playback_service import playback_service

# Regex for stripping track numbering and audio extensions
_EXT_RE = re.compile(r"\.(mp3|flac|wav|ogg|m4a|aac|opus|wma|aiff)$", re.IGNORECASE)
_TRACK_NUM_RE = re.compile(r"^(?:\d{1,3}[\s._-]+)+")


def clean_track_title(file_name: str) -> str:
    """Format human-readable track title from filename: '01. Bohemian Rhapsody.mp3' -> 'Bohemian Rhapsody'."""
    if not file_name:
        return "Без названия"
    name = _EXT_RE.sub("", file_name).strip()
    cleaned = _TRACK_NUM_RE.sub("", name).strip()
    return cleaned if cleaned else name


def _build_custom_cover_url(key: Optional[str]) -> Optional[str]:
    return f"/api/library/cover?key={key}&custom=1" if key else None


def _build_cover_url(url: Optional[str], custom_key: Optional[str]) -> Optional[str]:
    # Cache-only display URL: deterministic key from the remote URL.
    if custom_key or not url:
        return None
    from app.services.cover_cache_service import cover_cache
    return f"/api/library/cover?key={cover_cache.url_to_key(url)}"


def _cover_pair(url: Optional[str], custom_key: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    custom_url = _build_custom_cover_url(custom_key)
    display_url = custom_url if custom_url else _build_cover_url(url, None)
    return display_url, custom_url


class _SearchLRUCache:
    """Thread-safe LRU cache with TTL for search responses."""

    def __init__(self, capacity: int = 256, ttl_sec: float = 30.0):
        self._lock = threading.Lock()
        self._capacity = capacity
        self._ttl = ttl_sec
        self._cache: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            ts, val = entry
            if time.monotonic() - ts > self._ttl:
                del self._cache[key]
                return None
            return val

    def set(self, key: str, val: Any) -> None:
        with self._lock:
            if len(self._cache) >= self._capacity:
                # Evict oldest entry
                oldest_key = min(self._cache, key=lambda k: self._cache[k][0], default=None)
                if oldest_key:
                    del self._cache[oldest_key]
            self._cache[key] = (time.monotonic(), val)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_search_cache = _SearchLRUCache(capacity=256, ttl_sec=30.0)


class SearchService:
    """Service orchestrating search queries, metadata resolution, and caching."""

    @classmethod
    def sanitize_query(cls, query: Optional[str]) -> str:
        """Strip and normalize incoming search query."""
        if not query:
            return ""
        # Remove literal string 'null' or 'undefined' sent by buggy frontend clients
        trimmed = query.strip()
        if trimmed.lower() in ("null", "undefined"):
            return ""
        return trimmed[:200]

    @classmethod
    def search_unified(
        cls,
        db: Session,
        query: str,
        limit_artists: int = 6,
        limit_albums: int = 8,
        limit_tracks: int = 15,
    ) -> SearchUnifiedResponse:
        """Run unified search across artists, albums, and tracks in parallel."""
        q = cls.sanitize_query(query)
        if not q:
            return SearchUnifiedResponse(
                query="",
                artists=[],
                albums=[],
                tracks=[],
                total_artists=0,
                total_albums=0,
                total_tracks=0,
            )

        cache_key = f"unified:{q}:{limit_artists}:{limit_albums}:{limit_tracks}"
        cached = _search_cache.get(cache_key)
        if cached:
            return cached

        # 1. Artists
        raw_artists, total_artists = SearchRepository.search_artists(db, q, limit=limit_artists, offset=0)
        artists: List[SearchArtistItem] = []
        for a in raw_artists:
            cov, custom_cov = _cover_pair(a.get("image_url"), a.get("custom_image_key"))
            artists.append(SearchArtistItem(
                id=a["id"],
                display_name=a["display_name"],
                kind=a.get("kind", "artist"),
                image_url=cov,
                custom_image_url=custom_cov,
                album_count=a.get("album_count", 0),
                folder_id=a["folder_id"],
            ))

        # 2. Albums
        raw_albums, total_albums = SearchRepository.search_albums(db, q, limit=limit_albums, offset=0)
        albums: List[SearchAlbumItem] = []
        for alb in raw_albums:
            cov, custom_cov = _cover_pair(alb.get("cover_url"), alb.get("custom_image_key"))
            albums.append(SearchAlbumItem(
                id=alb["id"],
                title=alb["title"],
                year=alb.get("year"),
                artist_id=alb["artist_id"],
                artist_name=alb.get("artist_name"),
                cover_url=cov,
                custom_image_url=custom_cov,
                folder_id=alb["folder_id"],
            ))

        # 3. Tracks
        raw_tracks, total_tracks = SearchRepository.search_tracks(db, q, limit=limit_tracks, offset=0)
        track_ids = [t["id"] for t in raw_tracks]
        durations = playback_service.durations_map(db, track_ids) if track_ids else {}

        tracks: List[SearchTrackItem] = []
        for t in raw_tracks:
            cov, custom_cov = _cover_pair(t.get("cover_url"), t.get("custom_image_key"))
            tracks.append(SearchTrackItem(
                id=t["id"],
                title=clean_track_title(t["file_name"]),
                file_name=t["file_name"],
                size=t.get("size"),
                duration=durations.get(t["id"]),
                stream_url=f"/api/stream/{t['id']}",
                folder_id=t.get("folder_id"),
                album_id=t.get("album_id"),
                album_title=t.get("album_title"),
                artist_id=t.get("artist_id"),
                artist_name=t.get("artist_name"),
                cover_url=cov,
                custom_image_url=custom_cov,
            ))

        resp = SearchUnifiedResponse(
            query=q,
            artists=artists,
            albums=albums,
            tracks=tracks,
            total_artists=total_artists,
            total_albums=total_albums,
            total_tracks=total_tracks,
        )
        _search_cache.set(cache_key, resp)
        return resp

    @classmethod
    def search_tracks_paginated(
        cls,
        db: Session,
        query: str,
        limit: int = 30,
        offset: int = 0
    ) -> SearchPaginatedTracks:
        """Paginated search dedicated to audio tracks."""
        q = cls.sanitize_query(query)
        if not q:
            return SearchPaginatedTracks(query="", items=[], total=0, limit=limit, offset=offset)

        cache_key = f"tracks:{q}:{limit}:{offset}"
        cached = _search_cache.get(cache_key)
        if cached:
            return cached

        raw_tracks, total = SearchRepository.search_tracks(db, q, limit=limit, offset=offset)
        track_ids = [t["id"] for t in raw_tracks]
        durations = playback_service.durations_map(db, track_ids) if track_ids else {}

        items: List[SearchTrackItem] = []
        for t in raw_tracks:
            cov, custom_cov = _cover_pair(t.get("cover_url"), t.get("custom_image_key"))
            items.append(SearchTrackItem(
                id=t["id"],
                title=clean_track_title(t["file_name"]),
                file_name=t["file_name"],
                size=t.get("size"),
                duration=durations.get(t["id"]),
                stream_url=f"/api/stream/{t['id']}",
                folder_id=t.get("folder_id"),
                album_id=t.get("album_id"),
                album_title=t.get("album_title"),
                artist_id=t.get("artist_id"),
                artist_name=t.get("artist_name"),
                cover_url=cov,
                custom_image_url=custom_cov,
            ))

        resp = SearchPaginatedTracks(query=q, items=items, total=total, limit=limit, offset=offset)
        _search_cache.set(cache_key, resp)
        return resp

    @classmethod
    def search_artists_paginated(
        cls,
        db: Session,
        query: str,
        limit: int = 30,
        offset: int = 0
    ) -> SearchPaginatedArtists:
        """Paginated search dedicated to artists."""
        q = cls.sanitize_query(query)
        if not q:
            return SearchPaginatedArtists(query="", items=[], total=0, limit=limit, offset=offset)

        cache_key = f"artists:{q}:{limit}:{offset}"
        cached = _search_cache.get(cache_key)
        if cached:
            return cached

        raw_artists, total = SearchRepository.search_artists(db, q, limit=limit, offset=offset)
        items: List[SearchArtistItem] = []
        for a in raw_artists:
            cov, custom_cov = _cover_pair(a.get("image_url"), a.get("custom_image_key"))
            items.append(SearchArtistItem(
                id=a["id"],
                display_name=a["display_name"],
                kind=a.get("kind", "artist"),
                image_url=cov,
                custom_image_url=custom_cov,
                album_count=a.get("album_count", 0),
                folder_id=a["folder_id"],
            ))

        resp = SearchPaginatedArtists(query=q, items=items, total=total, limit=limit, offset=offset)
        _search_cache.set(cache_key, resp)
        return resp

    @classmethod
    def search_albums_paginated(
        cls,
        db: Session,
        query: str,
        limit: int = 30,
        offset: int = 0
    ) -> SearchPaginatedAlbums:
        """Paginated search dedicated to albums."""
        q = cls.sanitize_query(query)
        if not q:
            return SearchPaginatedAlbums(query="", items=[], total=0, limit=limit, offset=offset)

        cache_key = f"albums:{q}:{limit}:{offset}"
        cached = _search_cache.get(cache_key)
        if cached:
            return cached

        raw_albums, total = SearchRepository.search_albums(db, q, limit=limit, offset=offset)
        items: List[SearchAlbumItem] = []
        for alb in raw_albums:
            cov, custom_cov = _cover_pair(alb.get("cover_url"), alb.get("custom_image_key"))
            items.append(SearchAlbumItem(
                id=alb["id"],
                title=alb["title"],
                year=alb.get("year"),
                artist_id=alb["artist_id"],
                artist_name=alb.get("artist_name"),
                cover_url=cov,
                custom_image_url=custom_cov,
                folder_id=alb["folder_id"],
            ))

        resp = SearchPaginatedAlbums(query=q, items=items, total=total, limit=limit, offset=offset)
        _search_cache.set(cache_key, resp)
        return resp

    @classmethod
    def clear_cache(cls) -> None:
        """Invalidate in-memory search cache."""
        _search_cache.clear()
