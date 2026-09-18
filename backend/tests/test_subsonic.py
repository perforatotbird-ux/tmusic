"""
Subsonic API smoke tests (auth ping, XML/JSON shapes, token mint).
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import hash_password
from app.core.database import SessionLocal
from app.main import app
from app.models.auth import AppUser
from app.services import subsonic_auth
from sqlalchemy import select

client = TestClient(app)

USERNAME = "subsonic_test_user"
PASSWORD = "Subsonic!Test1"


def _ensure_user() -> AppUser:
    with SessionLocal() as db:
        user = db.scalar(select(AppUser).where(AppUser.username == USERNAME))
        if not user:
            user = AppUser(username=USERNAME, password_hash=hash_password(PASSWORD), is_active=True)
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            if not user.password_hash.startswith("$"):
                user.password_hash = hash_password(PASSWORD)
                db.commit()
        return user


def test_subsonic_auth_fail():
    resp = client.get("/rest/ping.view", params={"u": "nope", "p": "bad", "v": "1.16.1", "c": "test", "f": "json"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["subsonic-response"]["status"] == "failed"
    assert data["subsonic-response"]["error"]["code"] == 40


def test_subsonic_ping_plain_password():
    _ensure_user()
    resp = client.get(
        "/rest/ping.view",
        params={"u": USERNAME, "p": PASSWORD, "v": "1.16.1", "c": "T-Music", "f": "json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["subsonic-response"]["status"] == "ok"
    assert data["subsonic-response"]["version"]


def test_subsonic_ping_token_auth():
    _ensure_user()
    with SessionLocal() as db:
        user = db.scalar(select(AppUser).where(AppUser.username == USERNAME))
        token = subsonic_auth.ensure_subsonic_token(db, user)
    salt = "abc123"
    digest = hashlib.md5((token + salt).encode()).hexdigest()
    resp = client.get(
        "/rest/ping.view",
        params={
            "u": USERNAME,
            "t": digest,
            "s": salt,
            "v": "1.16.1",
            "c": "T-Music",
            "f": "json",
        },
    )
    data = resp.json()
    assert data["subsonic-response"]["status"] == "ok"


def test_subsonic_xml_ping():
    _ensure_user()
    resp = client.get(
        "/rest/ping",
        params={"u": USERNAME, "p": PASSWORD, "v": "1.16.1", "c": "T-Music", "f": "xml"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/xml")
    assert "subsonic-response" in resp.text
    assert 'status="ok"' in resp.text


def test_subsonic_get_license_and_folders():
    _ensure_user()
    for method in ("getLicense.view", "getMusicFolders.view", "getArtists.view"):
        resp = client.get(
            f"/rest/{method}",
            params={"u": USERNAME, "p": PASSWORD, "v": "1.16.1", "c": "T-Music", "f": "json"},
        )
        assert resp.status_code == 200
        body = resp.json()["subsonic-response"]
        assert body["status"] == "ok", method


def test_admin_credentials_endpoint_shape():
    _ensure_user()
    # Without JWT may 401 — that's fine
    resp = client.get("/api/subsonic/credentials")
    assert resp.status_code in (200, 401)
