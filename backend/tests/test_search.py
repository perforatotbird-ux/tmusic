"""
Unit and integration tests for T-Music search functionality.
Validates search schemas, repository queries, service logic, title cleaning,
special character escaping, and API routes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal
from app.services.search_service import SearchService, clean_track_title
from app.schemas.search import (
    SearchUnifiedResponse,
    SearchPaginatedTracks,
)


def test_clean_track_title():
    """Verify track title cleanup from various file naming conventions."""
    assert clean_track_title("01. Bohemian Rhapsody.mp3") == "Bohemian Rhapsody"
    assert clean_track_title("02 - Enter Sandman.flac") == "Enter Sandman"
    assert clean_track_title("03_Hotel_California.wav") == "Hotel_California"
    assert clean_track_title("004. Yesterday.m4a") == "Yesterday"
    assert clean_track_title("Track.ogg") == "Track"
    assert clean_track_title("01.mp3") == "01"
    assert clean_track_title("") == "Без названия"
    assert clean_track_title(None) == "Без названия"


def test_sanitize_query():
    """Verify search query sanitization and boundary limits."""
    assert SearchService.sanitize_query("  rock  ") == "rock"
    assert SearchService.sanitize_query(None) == ""
    assert SearchService.sanitize_query("null") == ""
    assert SearchService.sanitize_query("undefined") == ""
    assert SearchService.sanitize_query("   ") == ""
    long_q = "a" * 300
    assert len(SearchService.sanitize_query(long_q)) == 200


def test_empty_query_returns_empty_response():
    """Empty query should fast-path return empty items without database load."""
    with SessionLocal() as db:
        res = SearchService.search_unified(db, "")
        assert isinstance(res, SearchUnifiedResponse)
        assert res.query == ""
        assert res.artists == []
        assert res.albums == []
        assert res.tracks == []
        assert res.total_artists == 0
        assert res.total_albums == 0
        assert res.total_tracks == 0

        tracks = SearchService.search_tracks_paginated(db, "   ", limit=10)
        assert isinstance(tracks, SearchPaginatedTracks)
        assert tracks.items == []
        assert tracks.total == 0


def test_special_characters_escaping():
    """Special SQL LIKE characters (%, _, \\) must be escaped and not cause syntax errors."""
    with SessionLocal() as db:
        for special_q in ["%", "_", "\\", "%%%", "'; DROP TABLE vfiles; --", "rock%love", "a_b"]:
            res = SearchService.search_unified(db, special_q, limit_artists=5, limit_albums=5, limit_tracks=5)
            assert isinstance(res, SearchUnifiedResponse)
            # Response must be valid and query must match
            assert res.query == special_q.strip()


def test_search_unified_with_real_catalog():
    """Unified search against active catalog returns typed results with metadata."""
    with SessionLocal() as db:
        # Search for a broad term likely in library
        res = SearchService.search_unified(db, "rock", limit_artists=5, limit_albums=5, limit_tracks=10)
        assert isinstance(res, SearchUnifiedResponse)
        assert res.query == "rock"

        # Check tracks metadata bindings if tracks found
        for t in res.tracks:
            assert t.id > 0
            assert t.title
            assert t.file_name
            assert t.stream_url == f"/api/stream/{t.id}"
            # Track should either have an album or be an orphan track
            if t.album_id:
                assert t.album_title is not None


def test_paginated_search():
    """Pagination parameters limit and offset must be respected."""
    with SessionLocal() as db:
        p1 = SearchService.search_tracks_paginated(db, "love", limit=5, offset=0)
        assert len(p1.items) <= 5
        assert p1.limit == 5
        assert p1.offset == 0

        if p1.total > 5:
            p2 = SearchService.search_tracks_paginated(db, "love", limit=5, offset=5)
            assert p2.offset == 5
            # IDs on page 2 must be different from page 1
            p1_ids = {t.id for t in p1.items}
            p2_ids = {t.id for t in p2.items}
            assert len(p1_ids.intersection(p2_ids)) == 0


def test_search_cache():
    """Repeated calls within TTL should be served from memory cache."""
    with SessionLocal() as db:
        SearchService.clear_cache()
        res1 = SearchService.search_unified(db, "metallica", limit_artists=2, limit_albums=2, limit_tracks=5)
        res2 = SearchService.search_unified(db, "metallica", limit_artists=2, limit_albums=2, limit_tracks=5)
        assert res1 is res2  # Exactly the same object reference from cache
        SearchService.clear_cache()
