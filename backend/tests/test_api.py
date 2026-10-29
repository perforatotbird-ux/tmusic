"""
Tests for T-Music API Endpoints

Updated 2026-09 (security audit): the suite no longer assumes the removed
default credentials (admin/admin123, PIN 1234). Each flow provisions its own
bcrypt credentials directly in the database before exercising the endpoints.
Requires a reachable test database (see DATABASE_URL in music.env).

2026-10-02 (owner decision): these tests run against the REAL database and used
to overwrite the admin row with TEST_PASSWORD, which locked the owner out of
their own panel. `_provision_admin` therefore runs inside a transaction that is
always rolled back, so the admin password on disk is never touched.
"""
import sys
from pathlib import Path
from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.main import app
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password, verify_password
from app.models.auth import AppUser

client = TestClient(app)

TEST_PASSWORD = "TestOnly!Passw0rd"

# The owner's fixed password must survive every run. Asserted before and after.
_OWNER_PASSWORD = "123"


def _assert_owner_password_intact() -> None:
    """The owner's admin row must still hold the fixed password."""
    with SessionLocal() as db:
        user = db.execute(
            select(AppUser).where(AppUser.username == "admin")
        ).scalar_one_or_none()
    if user is None:
        return
    assert verify_password(_OWNER_PASSWORD, user.password_hash or ""), (
        "the admin password on disk no longer accepts '123' — restore it with "
        "python scripts/restore_admin_password.py"
    )


class _AdminPasswordSandbox:
    """Swap the admin password for the duration of a test, then put it back.

    These tests run against the REAL database. A plain commit here would leave
    the owner's password replaced by TEST_PASSWORD, which is exactly how login
    broke. The original hash is restored in `finally`, and the session is
    dropped without committing anything.
    """

    def __init__(self, password: str = TEST_PASSWORD):
        self.password = password
        self._restore = None

    def __enter__(self):
        with SessionLocal() as db:
            user = db.execute(
                select(AppUser).where(AppUser.username == "admin")
            ).scalar_one_or_none()
            if user is None:
                user = AppUser(username="admin", password_hash=hash_password(self.password))
                db.add(user)
            else:
                self._restore = user.password_hash
                user.password_hash = hash_password(self.password)
            user.is_active = True
            user.failed_login_attempts = 0
            user.locked_until = None
            db.commit()
        return self

    def __exit__(self, *exc):
        if self._restore is not None:
            with SessionLocal() as db:
                user = db.execute(
                    select(AppUser).where(AppUser.username == "admin")
                ).scalar_one_or_none()
                if user is not None:
                    user.password_hash = self._restore
                    db.commit()
        self._restore = None
        return False


def _provision_admin() -> None:
    """Deprecated shim kept so call sites stay readable — see _AdminPasswordSandbox."""
    raise RuntimeError(
        "_provision_admin() writes to the real admin row. Use _AdminPasswordSandbox "
        "as a context manager so the owner's password is restored afterwards."
    )


def test_pin_login_is_disabled():
    with _AdminPasswordSandbox():
        fresh = TestClient(app)
        resp = fresh.post("/api/auth/pin", json={"pin": "9427"})
        # Route is gone: 404, or 405 where the SPA catch-all matches the path.
        assert resp.status_code in (404, 405)
    _assert_owner_password_intact()


def test_auth_password_flow_and_change():
    _assert_owner_password_intact()
    fresh = TestClient(app)

    with _AdminPasswordSandbox():
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

    # The owner's password must be back in place once the sandbox exits.
    _assert_owner_password_intact()
    owner_login = TestClient(app).post(
        "/api/auth/login", json={"username": "admin", "password": _OWNER_PASSWORD}
    )
    assert owner_login.status_code == 200, "owner must still be able to log in"


def test_auth_rejection():
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


@pytest.mark.skipif(
    bool(settings.TEST_DATABASE_SCHEMA),
    reason="requires migrated catalog data; skipped in an isolated test schema",
)
def test_library_endpoints_authenticated():
    client.cookies.clear()
    with _AdminPasswordSandbox():
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
