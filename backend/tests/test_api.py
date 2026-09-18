"""
Tests for T-Music API Endpoints

Updated 2026-09 (security audit): the suite no longer assumes the removed
default credentials (admin/admin123, PIN 1234). Each flow provisions its own
bcrypt credentials directly in the database before exercising the endpoints.
Requires a reachable test database (see DATABASE_URL in music.env).
"""
import sys
from pathlib import Path
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.main import app
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.auth import AppUser

client = TestClient(app)

TEST_PASSWORD = "TestOnly!Passw0rd"


def _provision_admin() -> None:
    """Upsert the admin account with known bcrypt credentials for the test run."""
    with SessionLocal() as db:
        user = db.execute(
            select(AppUser).where(AppUser.username == "admin")
        ).scalar_one_or_none()
        if user is None:
            user = AppUser(username="admin", password_hash=hash_password(TEST_PASSWORD))
            db.add(user)
        else:
            user.password_hash = hash_password(TEST_PASSWORD)
        user.is_active = True
        user.failed_login_attempts = 0
        user.locked_until = None
        db.commit()


def test_pin_login_is_disabled():
    _provision_admin()
    fresh = TestClient(app)
    resp = fresh.post("/api/auth/pin", json={"pin": "9427"})
    # Route is gone: 404, or 405 where the SPA catch-all matches the path.
    assert resp.status_code in (404, 405)


def test_auth_password_flow_and_change():
    _provision_admin()
    fresh = TestClient(app)

    resp = fresh.post("/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Verify /api/auth/me with token
    me_resp = fresh.get("/api/auth/me", headers=headers)
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == "admin"

    # change-password requires the current password
    bad = fresh.post(
        "/api/auth/change-password",
        json={"current_password": "wrong", "new_password": "Another!Pass123"},
        headers=headers,
    )
    assert bad.status_code == 400

    ok = fresh.post(
        "/api/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": "Another!Pass123"},
        headers=headers,
    )
    assert ok.status_code == 200

    relogin = fresh.post("/api/auth/login", json={"username": "admin", "password": "Another!Pass123"})
    assert relogin.status_code == 200

    # Restore the original test password for other tests
    _provision_admin()


def test_auth_rejection():
    _provision_admin()
    # Fresh unauthenticated client
    unauthed_client = TestClient(app)

    # Incorrect password
    resp = unauthed_client.post("/api/auth/login", json={"username": "admin", "password": "definitely-wrong"})
    assert resp.status_code == 401

    # Protected endpoint without token
    lib_resp = unauthed_client.get("/api/library/artists")
    assert lib_resp.status_code == 401

    # Cover proxy without token (must not be an open proxy)
    cover_resp = unauthed_client.get("/api/library/cover?key=nonexistent")
    assert cover_resp.status_code == 401


def test_library_endpoints_authenticated():
    _provision_admin()
    client.cookies.clear()
    token_resp = client.post(
        "/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    token = token_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Stats
    stats_resp = client.get("/api/library/stats", headers=headers)
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["artists_count"] > 0
    assert stats["albums_count"] > 0

    # Artists
    artists_resp = client.get("/api/library/artists?limit=5", headers=headers)
    assert artists_resp.status_code == 200
    artists = artists_resp.json()
    assert len(artists) > 0
    assert "display_name" in artists[0]

    # Albums
    albums_resp = client.get("/api/library/albums?limit=5", headers=headers)
    assert albums_resp.status_code == 200
    albums = albums_resp.json()
    assert len(albums) > 0
    assert "title" in albums[0]
    assert "artist_id" in albums[0]
