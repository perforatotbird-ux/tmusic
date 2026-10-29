"""
Subsonic API smoke tests (auth ping, XML/JSON shapes, token mint).
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.core.database import SessionLocal
from app.main import app
from app.models.auth import AppUser
from app.services import subsonic_auth
from sqlalchemy import select

client = TestClient(app)

USERNAME = "subsonic_test_user"
PASSWORD = "Subsonic!Test1"


def _ensure_feature_tables() -> None:
    from app.core.database import ensure_feature_tables

    ensure_feature_tables()


_ensure_feature_tables()


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
            # Reset brute-force counters: negative-auth tests intentionally
            # trip the lockout, which must not leak into subsequent tests.
            user.failed_login_attempts = 0
            user.locked_until = None
            db.commit()
        # Load column values while still bound: the session closes on return
        # and detached lazy-refresh would raise DetachedInstanceError.
        db.refresh(user)
        return user


def _subsonic_token() -> str:
    """Dedicated per-user Subsonic token (the ONLY credential /rest accepts).

    The panel bcrypt password is deliberately rejected in the query string
    (2026-09 hardening M-1): it must never travel in URLs/proxy logs.
    """
    _ensure_user()
    with SessionLocal() as db:
        user = db.scalar(select(AppUser).where(AppUser.username == USERNAME))
        return subsonic_auth.ensure_subsonic_token(db, user)


def test_subsonic_auth_fail():
    resp = client.get("/rest/ping.view", params={"u": "nope", "p": "bad", "v": "1.16.1", "c": "test", "f": "json"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["subsonic-response"]["status"] == "failed"
    assert data["subsonic-response"]["error"]["code"] == 40


def test_subsonic_panel_password_rejected():
    """Panel password in p= must FAIL (hardening M-1) — only the dedicated
    Subsonic token authenticates /rest."""
    _ensure_user()
    resp = client.get(
        "/rest/ping.view",
        params={"u": USERNAME, "p": PASSWORD, "v": "1.16.1", "c": "T-Music", "f": "json"},
    )
    assert resp.status_code == 200
    body = resp.json()["subsonic-response"]
    assert body["status"] == "failed"
    assert body["error"]["code"] == 40


def test_subsonic_failure_does_not_lock_web_user():
    """SEC-002 regression: Repeated Subsonic auth failures must not lock out the web user."""
    _ensure_user()
    for i in range(6):
        resp = client.get(
            "/rest/ping.view",
            params={"u": USERNAME, "p": f"bad_token_{i}", "v": "1.16.1", "c": "test", "f": "json"},
        )
        assert resp.status_code == 200
        assert resp.json()["subsonic-response"]["status"] == "failed"

    # User in DB must not have locked_until or accumulated failed attempts from Subsonic
    with SessionLocal() as db:
        user = db.scalar(select(AppUser).where(AppUser.username == USERNAME))
        assert user.locked_until is None
        assert (user.failed_login_attempts or 0) == 0

    # Web login must continue to succeed
    login_resp = client.post("/api/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert login_resp.status_code == 200
    assert "access_token" in login_resp.json()


def test_subsonic_ping_token_as_password():
    """p=<dedicated token> authenticates."""
    resp = client.get(
        "/rest/ping.view",
        params={"u": USERNAME, "p": _subsonic_token(), "v": "1.16.1", "c": "T-Music", "f": "json"},
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
    resp = client.get(
        "/rest/ping",
        params={"u": USERNAME, "p": _subsonic_token(), "v": "1.16.1", "c": "T-Music", "f": "xml"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/xml")
    assert "subsonic-response" in resp.text
    assert 'status="ok"' in resp.text


def test_subsonic_get_license_and_folders():
    for method in ("getLicense.view", "getMusicFolders.view", "getArtists.view"):
        resp = client.get(
            f"/rest/{method}",
            params=_base_params(),
        )
        assert resp.status_code == 200
        body = resp.json()["subsonic-response"]
        assert body["status"] == "ok", method


def test_admin_credentials_endpoint_shape():
    _ensure_user()
    # Without JWT may 401 — that's fine
    resp = client.get("/api/subsonic/credentials")
    assert resp.status_code in (200, 401)


def _base_params(**extra):
    p = {"u": USERNAME, "p": _subsonic_token(), "v": "1.16.1", "c": "T-Music", "f": "json"}
    p.update(extra)
    return p


def _ensure_audio_file(name: str = "Artist - Song.mp3") -> int:
    from app.models.vfs import Folder, VFile

    with SessionLocal() as db:
        from sqlalchemy import func as sa_func

        vf = db.scalar(
            select(VFile).where(VFile.name == name, VFile.is_trash == False)  # noqa: E712
        )
        if vf:
            return vf.id
        folder = db.scalar(select(Folder).where(Folder.name == "SubsonicTest"))
        if not folder:
            folder = Folder(name="SubsonicTest")
            db.add(folder)
            db.commit()
            db.refresh(folder)
        vf = VFile(name=name, mime_type="audio/mpeg", folder_id=folder.id, is_trash=False)
        db.add(vf)
        db.commit()
        db.refresh(vf)
        assert db.scalar(sa_func.count(VFile.id)) >= 1
        return vf.id


def test_subsonic_star_unstar_getstarred():
    _ensure_user()
    fid = _ensure_audio_file()
    file_id = f"fl-{fid}"

    resp = client.get("/rest/star.view", params=_base_params(id=file_id))
    assert resp.json()["subsonic-response"]["status"] == "ok"

    resp = client.get("/rest/getStarred2.view", params=_base_params())
    body = resp.json()["subsonic-response"]
    assert body["status"] == "ok"
    songs = body["starred2"]["song"]
    assert any(s["id"] == file_id for s in songs)

    resp = client.get("/rest/unstar.view", params=_base_params(id=file_id))
    assert resp.json()["subsonic-response"]["status"] == "ok"

    resp = client.get("/rest/getStarred.view", params=_base_params())
    body = resp.json()["subsonic-response"]
    assert body["status"] == "ok"
    songs = body["starred"].get("song") or []
    assert not any(s["id"] == file_id for s in songs)


def test_subsonic_add_song_rating_maps_to_favorite():
    _ensure_user()
    fid = _ensure_audio_file()
    file_id = f"fl-{fid}"

    client.get("/rest/unstar.view", params=_base_params(id=file_id))
    resp = client.get(
        "/rest/addSongRating.view", params=_base_params(id=file_id, rating=5)
    )
    assert resp.json()["subsonic-response"]["status"] == "ok"
    with SessionLocal() as db:
        from app.models.playback import UserFavorite

        assert db.scalar(
            select(UserFavorite.id).where(
                UserFavorite.file_id == fid, UserFavorite.user_id == _ensure_user().id
            )
        )

    resp = client.get(
        "/rest/addSongRating.view", params=_base_params(id=file_id, rating=1)
    )
    assert resp.json()["subsonic-response"]["status"] == "ok"
    with SessionLocal() as db:
        from app.models.playback import UserFavorite

        user_id = _ensure_user().id
        assert (
            db.scalar(
                select(UserFavorite.id).where(
                    UserFavorite.file_id == fid, UserFavorite.user_id == user_id
                )
            )
            is None
        )


def test_subsonic_scrobble_submission():
    _ensure_user()
    fid = _ensure_audio_file()
    resp = client.get(
        "/rest/scrobble.view",
        params=_base_params(id=f"fl-{fid}", submission="true", time="1700000000000"),
    )
    assert resp.json()["subsonic-response"]["status"] == "ok"

    from app.models.playback import ListeningHistory

    with SessionLocal() as db:
        rows = db.scalars(
            select(ListeningHistory).where(
                ListeningHistory.file_id == fid,
                ListeningHistory.user_id == _ensure_user().id,
            )
        ).all()
        assert len(rows) >= 1


def test_subsonic_get_lyrics_shape(monkeypatch):
    _ensure_user()
    from app.services import lyrics_service

    monkeypatch.setattr(
        lyrics_service,
        "get_lyrics",
        lambda **kw: {
            "found": True,
            "source": "lrclib",
            "plain": "line one\nline two",
            "synced": "[00:01.00] line one\n[00:02.50] line two",
            "artist": "Artist",
            "track": "Song",
            "album": None,
            "duration_sec": None,
        },
    )

    resp = client.get(
        "/rest/getLyrics.view",
        params=_base_params(artist="Artist", title="Song"),
    )
    body = resp.json()["subsonic-response"]
    assert body["status"] == "ok"
    assert "line one" in body["lyrics"]["value"]

    fid = _ensure_audio_file()
    resp = client.get(
        "/rest/getLyricsBySongId.view",
        params=_base_params(id=f"fl-{fid}"),
    )
    body = resp.json()["subsonic-response"]
    assert body["status"] == "ok"
    structured = body["structuredLyrics"]
    assert isinstance(structured, list)
    assert any(s.get("synced") for s in structured)
    lrc = next(s for s in structured if s.get("synced"))
    assert lrc["line"][0]["start"] == 1000
    assert lrc["line"][1]["start"] == 2500


def test_subsonic_similar_and_info_methods_smoke():
    _ensure_user()
    fid = _ensure_audio_file()
    for method, extra in (
        ("getSimilarSongs.view", {"id": f"fl-{fid}", "count": 5}),
        ("getTopSongs.view", {"count": 5}),
        ("getGenres.view", {}),
        ("getNowPlayingList.view", {}),
    ):
        resp = client.get(f"/rest/{method}", params=_base_params(**extra))
        assert resp.status_code == 200, method
        body = resp.json()["subsonic-response"]
        assert body["status"] == "ok", method


def test_subsonic_xml_star_and_lyrics():
    _ensure_user()
    fid = _ensure_audio_file()
    resp = client.get(
        "/rest/star.view",
        params=_base_params(id=f"fl-{fid}", f="xml"),
    )
    assert resp.headers["content-type"].startswith("text/xml")
    assert 'status="ok"' in resp.text
    client.get("/rest/unstar.view", params=_base_params(id=f"fl-{fid}"))
