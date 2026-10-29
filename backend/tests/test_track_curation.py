"""Admin curation of individual tracks: rename + per-track cover art.

Covers the third catalog level (VFile) that the admin UI can now edit, mirroring
the artist/album behaviour covered by test_admin_catalog.py.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.auth import AppUser
from app.models.vfs import VFile
from app.repositories.search_repository import SearchRepository
from app.services import stream_service
from app.services.music_library_service import library_service

client = TestClient(app)


def _first_track_id() -> int:
    with SessionLocal() as db:
        track_id = db.scalar(
            select(VFile.id).where(VFile.mime_type.like("audio/%")).limit(1)
        )
    if track_id is None:
        pytest.skip("no audio track in the live library")
    return track_id


def _snapshot(track_id: int):
    """(name, custom_image_key) as stored right now."""
    with SessionLocal() as db:
        track = db.get(VFile, track_id)
        return track.name, track.custom_image_key


def _restore(track_id: int, name: str, custom_key) -> None:
    with SessionLocal() as db:
        track = db.get(VFile, track_id)
        if track:
            track.name = name
            track.custom_image_key = custom_key
            db.commit()


def _temp_admin(username: str, password: str) -> None:
    with SessionLocal() as db:
        db.add(AppUser(
            username=username,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        ))
        db.commit()


def _drop_user(username: str) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(AppUser).where(AppUser.username == username))
        if user:
            db.delete(user)
            db.commit()


def _admin_headers(username: str, password: str) -> dict:
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


# ─── Cover service: the third target_type ────────────────────────────────────

def test_set_custom_cover_accepts_track():
    track_id = _first_track_id()
    name, original_key = _snapshot(track_id)
    try:
        with SessionLocal() as db:
            library_service.set_custom_cover(db, "track", track_id, "TESTTRACKCOVER")
        with SessionLocal() as db:
            assert db.get(VFile, track_id).custom_image_key == "TESTTRACKCOVER"
        # Clearing works exactly like it does for artists/albums.
        with SessionLocal() as db:
            library_service.set_custom_cover(db, "track", track_id, None)
        with SessionLocal() as db:
            assert db.get(VFile, track_id).custom_image_key is None
    finally:
        _restore(track_id, name, original_key)


def test_set_custom_cover_rejects_unknown_target_type():
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            library_service.set_custom_cover(db, "playlist", 1, "KEY")
    assert exc.value.status_code == 400


def test_set_custom_cover_missing_track_is_404():
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            library_service.set_custom_cover(db, "track", 2147483000, "KEY")
    assert exc.value.status_code == 404


# ─── Rename: PATCH /api/admin/vfs/files/{id} ─────────────────────────────────

def test_track_rename_requires_admin():
    resp = client.patch("/api/admin/vfs/files/1", json={"name": "x.mp3"})
    assert resp.status_code in (401, 403)


def test_admin_can_rename_a_track_end_to_end():
    track_id = _first_track_id()
    name, key = _snapshot(track_id)
    username = f"track_rename_{uuid.uuid4().hex[:8]}"
    password = "TrackRename!Test1"
    _temp_admin(username, password)
    try:
        headers = _admin_headers(username, password)
        new_name = f"Renamed_{uuid.uuid4().hex[:6]}.mp3"
        resp = client.patch(
            f"/api/admin/vfs/files/{track_id}", json={"name": new_name}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == new_name
        with SessionLocal() as db:
            assert db.get(VFile, track_id).name == new_name
    finally:
        _restore(track_id, name, key)
        _drop_user(username)


def test_admin_track_rename_rejects_blank_name():
    track_id = _first_track_id()
    name, key = _snapshot(track_id)
    username = f"track_blank_{uuid.uuid4().hex[:8]}"
    password = "TrackBlank!Test1"
    _temp_admin(username, password)
    try:
        headers = _admin_headers(username, password)
        resp = client.patch(
            f"/api/admin/vfs/files/{track_id}", json={"name": "   "}, headers=headers
        )
        assert resp.status_code == 400
        with SessionLocal() as db:
            assert db.get(VFile, track_id).name == name
    finally:
        _restore(track_id, name, key)
        _drop_user(username)


def test_admin_file_list_exposes_the_custom_cover_url():
    track_id = _first_track_id()
    name, key = _snapshot(track_id)
    username = f"track_cover_{uuid.uuid4().hex[:8]}"
    password = "TrackCover!Test1"
    _temp_admin(username, password)
    try:
        with SessionLocal() as db:
            library_service.set_custom_cover(db, "track", track_id, "TESTPAYLOADKEY")
        headers = _admin_headers(username, password)
        resp = client.get(
            "/api/admin/vfs/files",
            params={"limit": 500, "search": name},
            headers=headers,
        )
        assert resp.status_code == 200
        row = next((item for item in resp.json()["items"] if item["id"] == track_id), None)
        assert row is not None
        assert row["custom_image_url"] == "/api/library/cover?key=TESTPAYLOADKEY&custom=1"
    finally:
        _restore(track_id, name, key)
        _drop_user(username)


# ─── Rendering: the track cover reaches the clients ──────────────────────────

def test_track_response_exposes_the_custom_cover_url():
    track_id = _first_track_id()
    name, key = _snapshot(track_id)
    try:
        with SessionLocal() as db:
            library_service.set_custom_cover(db, "track", track_id, "TESTRESPONSEKEY")
        with SessionLocal() as db:
            rows = stream_service.stream_service.build_track_responses(db, [db.get(VFile, track_id)])
        assert len(rows) == 1
        assert rows[0].custom_image_url == "/api/library/cover?key=TESTRESPONSEKEY&custom=1"
    finally:
        _restore(track_id, name, key)


def test_search_prefers_the_track_cover_over_the_inherited_one():
    track_id = _first_track_id()
    with SessionLocal() as db:
        track = db.get(VFile, track_id)
        name, key = track.name, track.custom_image_key
    query = Path(name).stem
    try:
        with SessionLocal() as db:
            library_service.set_custom_cover(db, "track", track_id, "TESTSEARCHKEY")
        with SessionLocal() as db:
            rows, _total = SearchRepository.search_tracks(db, query, limit=50, offset=0)
        row = next((r for r in rows if r["id"] == track_id), None)
        assert row is not None, "the fixture track must be reachable by its own name"
        assert row["custom_image_key"] == "TESTSEARCHKEY"
    finally:
        _restore(track_id, name, key)
