"""Artist scan as a background job: start, poll, isolate, bound.

The old contract was one blocking request; «Сканировать» read as «ничего не
выполняет» because the admin got no counter, stage or error for tens of seconds.
These tests pin the new contract:

* `POST /api/admin/catalog/artists/{id}/scan` returns immediately with a job
  (`accepted: true`) — never with the scan result itself;
* the same artist cannot be scanned twice at once, and only
  `MAX_CONCURRENT_SCANS` jobs may run together (409, not a silent drop);
* `GET /api/admin/catalog/scan/{job_id}` reports progress that reaches
  `done`, and is scoped to the admin who started it;
* an unknown job/artist is 404, not an endless spinner.
"""
import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.auth import AppUser
from app.services import lastfm_service
from app.services.music_library_service import CatalogAdminService
from app.services import scan_service
from app.services.scan_service import ScanBusy

client = TestClient(app)

REAL_ART = "https://lastfm.freetls.fastly.net/i/u/300x300/nevermind.png"


@pytest.fixture(autouse=True)
def _isolated_job_registry():
    """The job registry is module state; no test may leak a job into another.

    A running job would make the next test's `start_artist_scan` fail with a
    spurious 409, so anything still in flight is cancelled here.
    """
    yield
    for task in list(scan_service._TASKS.values()):
        task.cancel()
    scan_service._JOBS.clear()
    scan_service._TASKS.clear()
    scan_service._BY_ARTIST.clear()


def _stub_lastfm(monkeypatch, info):
    monkeypatch.setattr(lastfm_service, "metadata_ready", lambda: True)
    monkeypatch.setattr(lastfm_service, "get_artist_info", lambda name, force=False: info)


def _stub_cover_cache(monkeypatch):
    from app.services import cover_cache_service

    async def _warm(url):
        return None, None

    monkeypatch.setattr(cover_cache_service.cover_cache, "fetch_and_cache", _warm)


def _make_artist(db):
    """Create a throwaway artist card and return only its id.

    The ORM instance must not outlive the session that produced it: accessing
    `artist.id` after `close()` triggers a lazy refresh on a detached instance.
    """
    artist = CatalogAdminService.create_collection(db, f"T_JOB_{uuid.uuid4().hex[:8]}", "artist")
    return artist.id


def _drop_artist(artist_id):
    with SessionLocal() as db:
        try:
            CatalogAdminService.delete_artist(db, artist_id)
        except Exception:
            pass


def _temp_user(username: str) -> str:
    password = uuid.uuid4().hex[:10] + "!A1"
    with SessionLocal() as db:
        db.add(AppUser(
            username=username,
            password_hash=hash_password(password),
            role="admin",
            is_active=True,
        ))
        db.commit()
    return password


def _drop_user(username: str):
    with SessionLocal() as db:
        user = db.scalar(select(AppUser).where(AppUser.username == username))
        if user:
            db.delete(user)
            db.commit()


def _headers(username: str, password: str) -> dict:
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _wait_done(job_id: str, headers: dict, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    payload = {}
    while time.time() < deadline:
        payload = client.get(f"/api/admin/catalog/scan/{job_id}", headers=headers).json()
        if payload["status"] != "running":
            return payload
        time.sleep(0.05)
    return payload


def test_scan_returns_immediately_and_reaches_done(monkeypatch):
    """POST answers at once; the job carries progress through to `done`."""
    with SessionLocal() as db:
        artist = _make_artist(db)
    try:
        _stub_lastfm(monkeypatch, {
            "summary": "Bio from the job test.",
            "mbid": "mbid-job-1",
            "image_url": REAL_ART,
        })
        _stub_cover_cache(monkeypatch)
        username = f"scan_job_{uuid.uuid4().hex[:8]}"
        password = _temp_user(username)
        try:
            headers = _headers(username, password)
            started = client.post(f"/api/admin/catalog/artists/{artist}/scan", headers=headers)
            assert started.status_code == 200, started.text
            body = started.json()
            assert body["accepted"] is True
            assert body["status"] == "running"
            assert body["progress"]["stage"] in ("idle", "artist")
            # The response must not contain the scan result itself.
            assert "report" in body and body["report"] is None

            final = _wait_done(body["job_id"], headers)
            assert final["status"] == "completed", final
            assert final["progress"]["stage"] == "done"
            assert final["progress"]["current"] == final["progress"]["total"]
            assert final["report"]["updated"] is True
            assert final["report"]["fields"] == ["bio", "mbid", "image_url"]
        finally:
            _drop_user(username)
    finally:
        _drop_artist(artist)


def _slow_scan(monkeypatch, delay: float = 0.25):
    """Replace the scan body with a slow stub so jobs stay in flight.

    Done at the service level rather than through Last.fm: the stub is `async`,
    so it yields to the loop instead of blocking it, which is what makes the
    concurrency checks deterministic.
    """

    async def _stub(db, artist_id, report):
        report("artist", "…", 0, 1)
        await asyncio.sleep(delay)
        report("done", "Готово", 1, 1)
        return {"ok": True, "updated": False, "fields": []}

    monkeypatch.setattr(scan_service, "scan_artist_in_db", _stub)


async def _drain(actor_id: int, timeout: float = 10.0) -> None:
    """Wait until no scan job is running, so tests cannot leak jobs into others."""
    deadline = time.time() + timeout
    while time.time() < deadline and scan_service.active_jobs() > 0:
        await asyncio.sleep(0.02)


def test_duplicate_scan_of_the_same_artist_is_rejected(monkeypatch):
    """A job in flight is visible: a second click gets 409, not a silent queue."""
    with SessionLocal() as db:
        artist = _make_artist(db)
    actor = 990001
    try:
        _slow_scan(monkeypatch)

        async def scenario():
            first = scan_service.start_artist_scan(artist, "Dup", actor)
            assert first["status"] == "running"
            with pytest.raises(ScanBusy) as exc:
                scan_service.start_artist_scan(artist, "Dup", actor)
            assert "уже выполняется" in exc.value.detail
            assert exc.value.status == 409

            await _drain(actor)
            job = scan_service.get_job(first["job_id"], actor)
            assert job.status == "completed"
            # Once finished the artist may be scanned again.
            again = scan_service.start_artist_scan(artist, "Dup", actor)
            assert again["status"] == "running"
            await _drain(actor)

        asyncio.run(scenario())
    finally:
        _drop_artist(artist)


def test_concurrent_scan_cap_is_enforced(monkeypatch):
    """Only MAX_CONCURRENT_SCANS jobs run at once; the rest get a clear 409."""
    limit = scan_service.MAX_CONCURRENT_SCANS
    artists = []
    for _ in range(limit + 1):
        with SessionLocal() as db:
            artists.append(_make_artist(db))
    actor = 990002
    try:
        _slow_scan(monkeypatch, delay=0.3)

        async def scenario():
            accepted = []
            for artist in artists[:limit]:
                accepted.append(scan_service.start_artist_scan(artist, "Cap", actor))
            with pytest.raises(ScanBusy) as exc:
                scan_service.start_artist_scan(artists[limit], "Cap", actor)
            assert "Одновременно" in exc.value.detail
            assert len(accepted) == limit
            await _drain(actor)
            for snapshot in accepted:
                job = scan_service.get_job(snapshot["job_id"], actor)
                assert job.status == "completed", job.snapshot()

        asyncio.run(scenario())
    finally:
        for artist in artists:
            _drop_artist(artist)


def test_scan_job_is_actor_scoped(monkeypatch):
    """Another admin polling the same job id gets 404, not the report."""
    with SessionLocal() as db:
        artist = _make_artist(db)
    try:
        _stub_lastfm(monkeypatch, {"summary": "Actor scoped bio.", "mbid": None, "image_url": None})
        _stub_cover_cache(monkeypatch)

        owner = f"scan_owner_{uuid.uuid4().hex[:8]}"
        owner_pw = _temp_user(owner)
        other = f"scan_other_{uuid.uuid4().hex[:8]}"
        other_pw = _temp_user(other)
        try:
            owner_headers = _headers(owner, owner_pw)
            other_headers = _headers(other, other_pw)

            started = client.post(f"/api/admin/catalog/artists/{artist}/scan", headers=owner_headers)
            assert started.status_code == 200, started.text
            job_id = started.json()["job_id"]

            final = _wait_done(job_id, owner_headers)
            assert final["status"] == "completed", final
            # Same job, different actor.
            assert client.get(f"/api/admin/catalog/scan/{job_id}", headers=other_headers).status_code == 404
            # Owner can still read it.
            assert client.get(f"/api/admin/catalog/scan/{job_id}", headers=owner_headers).status_code == 200
        finally:
            _drop_user(owner)
            _drop_user(other)
    finally:
        _drop_artist(artist)


def test_unknown_scan_job_is_404():
    username = f"scan_404_{uuid.uuid4().hex[:8]}"
    password = _temp_user(username)
    try:
        headers = _headers(username, password)
        resp = client.get("/api/admin/catalog/scan/scan-doesnotexist", headers=headers)
        assert resp.status_code == 404
    finally:
        _drop_user(username)


def test_scan_unknown_artist_is_404(monkeypatch):
    _stub_lastfm(monkeypatch, {"summary": "", "mbid": None, "image_url": None})
    username = f"scan_404a_{uuid.uuid4().hex[:8]}"
    password = _temp_user(username)
    try:
        headers = _headers(username, password)
        resp = client.post("/api/admin/catalog/artists/2147483000/scan", headers=headers)
        assert resp.status_code == 404
    finally:
        _drop_user(username)


def test_scan_without_metadata_provider_is_503(monkeypatch):
    with SessionLocal() as db:
        artist = _make_artist(db)
    try:
        monkeypatch.setattr(lastfm_service, "metadata_ready", lambda: False)
        username = f"scan_503_{uuid.uuid4().hex[:8]}"
        password = _temp_user(username)
        try:
            headers = _headers(username, password)
            resp = client.post(f"/api/admin/catalog/artists/{artist}/scan", headers=headers)
            assert resp.status_code == 503
        finally:
            _drop_user(username)
    finally:
        _drop_artist(artist)


def test_failed_scan_surfaces_its_error(monkeypatch):
    """A provider miss ends as a failed job with a message, never a spinner."""
    with SessionLocal() as db:
        artist = _make_artist(db)
    try:
        _stub_lastfm(monkeypatch, None)  # Last.fm has no such artist
        username = f"scan_fail_{uuid.uuid4().hex[:8]}"
        password = _temp_user(username)
        try:
            headers = _headers(username, password)
            started = client.post(f"/api/admin/catalog/artists/{artist}/scan", headers=headers)
            assert started.status_code == 200, started.text
            final = _wait_done(started.json()["job_id"], headers)
            assert final["status"] == "failed", final
            assert final["error"]
            assert final["progress"]["stage"] == "failed"
        finally:
            _drop_user(username)
    finally:
        _drop_artist(artist)


def test_progress_reports_cover_stage_with_a_real_total(monkeypatch):
    """The bar must be able to reach 100%: albums are counted up-front."""
    from app.models import vfs
    from app.models.music import MusicAlbum

    with SessionLocal() as db:
        artist = _make_artist(db)
        folder = vfs.Folder(name=f"T_JOB_A_{uuid.uuid4().hex[:6]}")
        db.add(folder)
        db.flush()
        db.add(MusicAlbum(folder_id=folder.id, artist_id=artist, title="Nevermind"))
        db.commit()
    try:
        _stub_lastfm(monkeypatch, {"summary": "", "mbid": None, "image_url": None})
        _stub_cover_cache(monkeypatch)
        monkeypatch.setattr(
            lastfm_service, "get_album_info", lambda a, t, force=False: {"image_url": REAL_ART}
        )

        stages = []

        def report(stage, message, current=None, total=None):
            stages.append((stage, current, total))

        import asyncio

        with SessionLocal() as db:
            asyncio.run(scan_service.scan_artist_in_db(db, artist, report))

        album_stages = [s for s in stages if s[0] == "albums"]
        assert album_stages, stages
        assert album_stages[0][2] == 1, album_stages  # total is known before the work
        assert album_stages[-1][1] == album_stages[-1][2]  # the bar completes
        assert stages[-1][0] == "done"
        with SessionLocal() as db:
            album = db.scalar(select(MusicAlbum).where(MusicAlbum.artist_id == artist))
            assert album.cover_url == REAL_ART
    finally:
        _drop_artist(artist)
