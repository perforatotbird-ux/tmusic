"""
Last.fm signature + track meta parsing + config readiness (no network).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.services.lastfm_service import (
    _api_sig,
    _sanitize_cached,
    clean_bio,
    is_placeholder_image,
    metadata_ready,
    parse_track_meta,
    pick_image,
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


# ── Provider guards ──────────────────────────────────────────────────────────
# artist.getInfo stopped serving artist imagery: every artist without a picture
# gets the same "star" asset. Writing it into a row collapsed every scanned
# artist onto one cached cover, because the cover key is derived from the URL.

PLACEHOLDER = (
    "https://lastfm.freetls.fastly.net/i/u/34s/2a96cbd8b46e442fc41c2b86b821562f.png"
)
REAL_SMALL = "https://lastfm.freetls.fastly.net/i/u/34s/nevermind.png"
REAL_LARGE = "https://lastfm.freetls.fastly.net/i/u/300x300/nevermind.png"


def test_is_placeholder_image():
    assert is_placeholder_image(None) is True
    assert is_placeholder_image("") is True
    assert is_placeholder_image(PLACEHOLDER) is True
    # Case-insensitive: Last.fm has served the same marker with different casing.
    assert is_placeholder_image(PLACEHOLDER.upper()) is True
    assert is_placeholder_image(REAL_LARGE) is False


def test_pick_image_skips_placeholder_and_prefers_largest():
    images = [
        {"size": "small", "#text": PLACEHOLDER},
        {"size": "medium", "#text": REAL_SMALL},
        {"size": "mega", "#text": REAL_LARGE},
        {"size": "large", "#text": PLACEHOLDER},
    ]
    assert pick_image(images) == REAL_LARGE


def test_pick_image_returns_none_when_only_placeholder():
    # This is the real-world case: artist.getInfo answers with the placeholder
    # in every size, so callers must be told "no artwork" instead of the star.
    images = [
        {"size": "small", "#text": PLACEHOLDER},
        {"size": "extralarge", "#text": PLACEHOLDER},
        {"size": "", "#text": ""},
    ]
    assert pick_image(images) is None
    assert pick_image(None) is None
    assert pick_image([]) is None
    # Malformed entries must not crash a scan.
    assert pick_image([None, "nope", {"size": "large"}]) is None


def test_clean_bio_strips_lastfm_stub():
    assert clean_bio("Kurt Cobain was an American musician. Read more on Last.fm") == (
        "Kurt Cobain was an American musician."
    )
    # Stub-only summary means "no biography at all", not a biography.
    assert clean_bio("Read more on Last.fm") == ""
    assert clean_bio("  Read more on Last.fm  ") == ""
    assert clean_bio(None) == ""
    assert clean_bio("") == ""
    # Unrelated text is untouched.
    assert clean_bio("Plain biography") == "Plain biography"


def test_sanitize_cached_reapplies_guards_to_stale_entries():
    """The Last.fm disk cache lives 14 days — old entries must not re-inject junk."""
    stale = {
        "summary": "Some biography Read more on Last.fm",
        "image_url": PLACEHOLDER,
        "mbid": "abc",
    }
    clean = _sanitize_cached(stale)
    assert clean["image_url"] is None
    assert clean["summary"] == "Some biography"
    assert clean["mbid"] == "abc"
    # The caller's dict is not mutated in place.
    assert stale["image_url"] == PLACEHOLDER

    untouched = {"image_url": REAL_LARGE, "summary": "Bio"}
    assert _sanitize_cached(untouched) == untouched
    # Non-dict values pass straight through (e.g. a cached miss).
    assert _sanitize_cached(None) is None
    assert _sanitize_cached("nope") == "nope"


def test_sanitize_cached_does_not_reshape_payloads():
    """A cache hit must equal the fetch that produced it.

    Regression: nulling `image_url` unconditionally added the key to entries
    that never had it, so a cached read no longer matched a fresh fetch.
    """
    assert _sanitize_cached({"name": "Queen", "similar": ["aaa"]}) == {
        "name": "Queen",
        "similar": ["aaa"],
    }
    # A key present with an empty value is still normalised to None.
    assert _sanitize_cached({"name": "Queen", "image_url": ""})["image_url"] is None
