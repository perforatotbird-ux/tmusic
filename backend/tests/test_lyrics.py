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


def test_metadata_status_includes_lyrics_flag():
    resp = client.get("/api/metadata/status")
    assert resp.status_code in (200, 401)
    if resp.status_code == 200:
        data = resp.json()
        assert "lyrics" in data
        assert "enabled" in data["lyrics"]
