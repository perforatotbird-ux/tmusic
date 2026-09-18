"""
Last.fm signature + track meta parsing + config readiness (no network).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.services.lastfm_service import (
    _api_sig,
    metadata_ready,
    parse_track_meta,
    scrobble_ready,
)


def test_api_sig_stable():
    params = {
        "method": "track.scrobble",
        "artist": "Artist",
        "track": "Song",
        "api_key": "key",
        "sk": "session",
        "format": "json",
    }
    sig = _api_sig(params, "secret")
    assert isinstance(sig, str) and len(sig) == 32
    sig2 = _api_sig(dict(reversed(list(params.items()))), "secret")
    assert sig == sig2


def test_parse_filename_artist_title():
    artist, track, album = parse_track_meta("Daft Punk - Around the World.mp3")
    assert artist == "Daft Punk"
    assert track == "Around the World"
    assert album is None


def test_parse_hints_override():
    artist, track, album = parse_track_meta(
        "01 - intro.flac",
        artist_hint="Queen",
        track_hint="Bohemian Rhapsody",
        album_hint="A Night at the Opera",
    )
    assert artist == "Queen"
    assert track == "Bohemian Rhapsody"
    assert album == "A Night at the Opera"


def test_parse_plain_name():
    artist, track, album = parse_track_meta("mystery_track.mp3")
    assert artist == "Unknown"
    assert track == "mystery_track"


def test_lastfm_secret_alias():
    # settings.lastfm_secret accepts LASTFM_API_SECRET or LASTFM_SHARED_SECRET
    secret = settings.lastfm_secret
    assert secret is None or isinstance(secret, str)
    if settings.LASTFM_API_KEY and (settings.LASTFM_SHARED_SECRET or settings.LASTFM_API_SECRET):
        assert metadata_ready() is True
    if not settings.LASTFM_SESSION_KEY:
        assert scrobble_ready() is False


def test_metadata_status_endpoint_shape():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/metadata/status")
    assert resp.status_code in (200, 401)
    if resp.status_code == 200:
        data = resp.json()
        assert "lastfm" in data and "discogs" in data
        # Never leak raw secrets
        blob = resp.text
        for candidate in filter(None, [
            settings.LASTFM_API_KEY,
            settings.lastfm_secret,
            settings.DISCOGS_CONSUMER_SECRET,
        ]):
            assert candidate not in blob
