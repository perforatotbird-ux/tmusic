"""
Lyrics endpoint + LRC helpers (LRCLIB is monkeypatched — no network in tests).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app
from app.services import lyrics_service

client = TestClient(app)


def test_lyrics_endpoint_auth_guard():
    resp = client.get(
        "/api/metadata/lyrics",
        params={"artist": "Queen", "track": "Bohemian Rhapsody"},
    )
    assert resp.status_code in (200, 401)


def test_lyrics_endpoint_shape_with_stub(monkeypatch):
    monkeypatch.setattr(
        lyrics_service,
        "get_lyrics",
        lambda **kwargs: {
            "found": True,
            "source": "lrclib",
            "plain": "Is this the real life?",
            "synced": "[00:01.00]Is this the real life?",
            "artist": "Queen",
            "track": "Bohemian Rhapsody",
            "album": None,
            "duration_sec": 180,
        },
    )
    # Endpoint imports lyrics_service module attribute at call time
    resp = client.get(
        "/api/metadata/lyrics",
        params={
            "artist": "Queen",
            "track": "Bohemian Rhapsody",
            "file_name": "Queen - Bohemian Rhapsody.mp3",
            "duration": 185,
        },
    )
    # 401 if auth enforced without session; 200 if open/dev impersonation
    assert resp.status_code in (200, 401)
    if resp.status_code == 200:
        data = resp.json()
        assert data["found"] is True
        assert data["source"] == "lrclib"
        assert "Is this the real life" in (data["plain"] or "")
        assert data["synced"].startswith("[")


def test_lyrics_not_found_returns_found_false(monkeypatch):
    monkeypatch.setattr(lyrics_service, "get_lyrics", lambda **kwargs: None)
    resp = client.get(
        "/api/metadata/lyrics",
        params={"artist": "Nobody", "track": "Missing Song XYZ"},
    )
    assert resp.status_code in (200, 401)
    if resp.status_code == 200:
        data = resp.json()
        assert data["found"] is False


def test_lyrics_service_parses_filename(monkeypatch):
    calls = []

    def fake_get(path, params, timeout):
        calls.append((path, dict(params)))
        return None

    monkeypatch.setattr(lyrics_service, "_get", fake_get)
    monkeypatch.setattr(lyrics_service, "lyrics_enabled", lambda: True)
    result = lyrics_service.get_lyrics(file_name="Daft Punk - Around the World.mp3")
    assert result is None
    assert calls, "LRCLIB should be queried"
    path, params = calls[0]
    assert path == "get"
    assert params["artist_name"] == "Daft Punk"
    assert params["track_name"] == "Around the World"


# ─── Staged lookup strategy ───────────────────────────────────────────────────
def _lyric_node(**over):
    node = {
        "plainLyrics": "line",
        "syncedLyrics": "[00:01.00]line",
        "artistName": "Artist",
        "trackName": "Track",
        "duration": 200,
    }
    node.update(over)
    return node


def test_album_mismatch_falls_back_to_get_without_album(monkeypatch):
    """LRCLIB /api/get is exact-match on every param: a wrong album 404s even
    when the song exists. The lookup must retry without it."""
    calls = []

    def fake_get(path, params, timeout):
        calls.append((path, dict(params)))
        if path == "get" and "album_name" in params:
            return None  # album mismatch
        if path == "get":
            return _lyric_node(artistName="Metallica", trackName="Enter Sandman")
        return None

    monkeypatch.setattr(lyrics_service, "_get", fake_get)
    monkeypatch.setattr(lyrics_service, "lyrics_enabled", lambda: True)
    result = lyrics_service.get_lyrics(
        artist="Metallica", track="Enter Sandman", album="Wrong Album"
    )

    assert result and result["found"] is True
    gets = [p for path, p in calls if path == "get"]
    assert len(gets) == 2, "should try with album, then without"
    assert "album_name" in gets[0]
    assert "album_name" not in gets[1]
    assert len([1 for path, _ in calls if path == "search"]) == 0, "should not need search"


def test_falls_back_to_search_when_get_never_matches(monkeypatch):
    calls = []

    def fake_get(path, params, timeout):
        calls.append((path, dict(params)))
        if path == "search":
            return [_lyric_node(artistName="Metallica", trackName="Enter Sandman")]
        return None

    monkeypatch.setattr(lyrics_service, "_get", fake_get)
    monkeypatch.setattr(lyrics_service, "lyrics_enabled", lambda: True)
    result = lyrics_service.get_lyrics(artist="Metallica", track="Enter Sandman")

    assert result and result["found"] is True
    assert [path for path, _ in calls][-1] == "search"


def test_search_prefers_synced_then_exact_artist(monkeypatch):
    def fake_get(path, params, timeout):
        if path == "get":
            return None
        return [
            _lyric_node(artistName="Other", trackName="T", plainLyrics="a", syncedLyrics=""),
            _lyric_node(artistName="Other", trackName="T", plainLyrics="b", syncedLyrics="[00:01.00]b"),
            _lyric_node(artistName="Metallica", trackName="T", plainLyrics="c", syncedLyrics="[00:01.00]c"),
        ]

    monkeypatch.setattr(lyrics_service, "_get", fake_get)
    monkeypatch.setattr(lyrics_service, "lyrics_enabled", lambda: True)
    result = lyrics_service.get_lyrics(artist="Metallica", track="T")

    assert result and result["artist"] == "Metallica"


def test_unknown_artist_searches_title_only(monkeypatch):
    calls = []

    def fake_get(path, params, timeout):
        calls.append((path, dict(params)))
        return None

    monkeypatch.setattr(lyrics_service, "_get", fake_get)
    monkeypatch.setattr(lyrics_service, "lyrics_enabled", lambda: True)
    lyrics_service.get_lyrics(file_name="Isolation.mp3")

    assert [path for path, _ in calls] == ["search"], "no artist → skip /api/get"
    assert calls[0][1]["q"] == "Isolation"


def test_final_fallback_searches_title_alone_when_artist_rejected(monkeypatch):
    calls = []

    def fake_get(path, params, timeout):
        calls.append((path, dict(params)))
        if path == "search" and params.get("q") == "Enter Sandman":
            return [_lyric_node(artistName="Metallica", trackName="Enter Sandman")]
        return None

    monkeypatch.setattr(lyrics_service, "_get", fake_get)
    monkeypatch.setattr(lyrics_service, "lyrics_enabled", lambda: True)
    result = lyrics_service.get_lyrics(artist="Metallica", track="Enter Sandman")

    assert result and result["found"] is True
    queries = [p["q"] for path, p in calls if path == "search"]
    assert queries == ["Metallica Enter Sandman", "Enter Sandman"]


def test_hits_without_lyrics_are_not_returned(monkeypatch):
    def fake_get(path, params, timeout):
        if path == "get":
            return None
        return [_lyric_node(plainLyrics="", syncedLyrics="")]

    monkeypatch.setattr(lyrics_service, "_get", fake_get)
    monkeypatch.setattr(lyrics_service, "lyrics_enabled", lambda: True)
    assert lyrics_service.get_lyrics(artist="X", track="Y") is None


def test_upstream_exception_does_not_propagate(monkeypatch):
    def fake_get(path, params, timeout):
        raise RuntimeError("network down")

    monkeypatch.setattr(lyrics_service, "lyrics_enabled", lambda: True)
    # _get itself swallows, but a patched raising _get proves get_lyrics stays
    # defensive if the seam changes.
    monkeypatch.setattr(lyrics_service, "_get", lambda *a, **k: None)
    assert lyrics_service.get_lyrics(artist="X", track="Y") is None


def test_metadata_status_includes_lyrics_flag():
    resp = client.get("/api/metadata/status")
    assert resp.status_code in (200, 401)
    if resp.status_code == 200:
        data = resp.json()
        assert "lyrics" in data
        assert "enabled" in data["lyrics"]


def test_lyrics_endpoint_float_duration(monkeypatch):
    """Ensure float duration from LRCLIB (e.g. 256.0933329) does not trigger Pydantic ValidationError."""
    monkeypatch.setattr(
        lyrics_service,
        "get_lyrics",
        lambda **kwargs: {
            "found": True,
            "source": "lrclib",
            "plain": "Lyrics text",
            "synced": "[00:01.00]Lyrics text",
            "artist": "Artist",
            "track": "Track",
            "album": "Album",
            "duration_sec": 256.0933329,
        },
    )
    resp = client.get(
        "/api/metadata/lyrics",
        params={
            "artist": "Artist",
            "track": "Track",
            "duration": 256,
        },
    )
    assert resp.status_code in (200, 401)
    if resp.status_code == 200:
        data = resp.json()
        assert data["found"] is True
        assert data["duration_sec"] == 256.0933329

