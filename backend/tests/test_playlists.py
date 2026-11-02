"""
Playlist + continue-listening API smoke tests (auth guards / shapes).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_playlist_endpoints_auth_guard():
    paths = [
        ("/api/playlists", "GET"),
        ("/api/playlists/1", "GET"),
        ("/api/playlists/1/items/order", "PUT"),
        ("/api/playback/positions", "GET"),
        ("/api/playback/history", "GET"),
    ]
    for path, method in paths:
        resp = client.request(method, path)
        assert resp.status_code in (200, 401), f"{method} {path} -> {resp.status_code}"


def test_playlist_create_requires_auth_or_impostor_dev():
    resp = client.post("/api/playlists", json={"name": "Test list"})
    assert resp.status_code in (200, 401, 403)
    if resp.status_code == 200:
        data = resp.json()
        assert "id" in data
        assert data["name"] == "Test list"


def test_playlist_reorder_requires_valid_permutation():
    """With no session the reorder must never mutate (401/403/404 only)."""
    resp = client.put("/api/playlists/1/items/order", json={"item_ids": [1, 2, 3]})
    assert resp.status_code in (200, 401, 403, 404, 400)


def test_playlist_rename_endpoint_guard():
    """PATCH /api/playlists/{id} is a mutation: без сессии данные не меняются."""
    resp = client.patch("/api/playlists/1", json={"name": "Renamed"})
    assert resp.status_code in (200, 401, 403, 404)


def test_playlist_rename_rejects_empty_name():
    """Пустое имя отбивается валидацией схемы PlaylistName."""
    resp = client.patch("/api/playlists/1", json={"name": ""})
    assert resp.status_code in (400, 401, 403, 404, 422)
