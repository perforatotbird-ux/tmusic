"""
T-Music catalog scan jobs (Last.fm refresh with progress).

Why this exists (2026-10-12): «Сканировать» used to be one blocking request.
Refreshing an artist means up to `MAX_ALBUMS_PER_SCAN` Last.fm album lookups —
tens of seconds — during which the admin saw nothing at all: no counter, no
stage, no error. The request either succeeded silently or timed out, which is
indistinguishable from «она ничего не выполняет».

The scan is now a background job with pollable progress. The state shape mirrors
the VFS import progress (`current`/`total`/`stage`/`message`) so the UI has a
single progress contract, and all business logic lives here rather than in the
router (ADR-001 — no god objects in `app/api`).
"""
import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from app.services import lastfm_service
from app.services.music_library_service import library_service
from app.utils.time import utcnow

logger = logging.getLogger("tmusic.catalog.scan")

# A scan is a synchronous admin action, so the per-album Last.fm calls are
# bounded: only albums with no usable cover of their own are queried, and at
# most this many per run (~0.2 s each).
MAX_ALBUMS_PER_SCAN = 12

# Concurrent Last.fm jobs. Generous enough that an admin can queue a few
# artists, bounded so a burst of scans cannot hammer the provider.
MAX_CONCURRENT_SCANS = 3

# Finished jobs are kept this long so a poll arriving after the UI stopped
# (or a second tab) can still read the report.
JOB_TTL_SEC = 600


@dataclass
class ScanJob:
    job_id: str
    artist_id: int
    display_name: str
    actor_id: int
    created_at: float = field(default_factory=time.time)
    status: str = "running"  # running | completed | failed
    error: Optional[str] = None
    report: Optional[Dict[str, Any]] = None
    progress: Dict[str, Any] = field(
        default_factory=lambda: {"current": 0, "total": 0, "stage": "idle", "message": ""}
    )

    def snapshot(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "artist_id": self.artist_id,
            "display_name": self.display_name,
            "status": self.status,
            "error": self.error,
            "report": self.report,
            "progress": dict(self.progress),
        }


_JOBS: Dict[str, ScanJob] = {}
_JOBS_LOCK = threading.RLock()
_TASKS: Dict[str, asyncio.Task] = {}
_BY_ARTIST: Dict[int, str] = {}


def _reap() -> None:
    """Drop finished jobs that nobody polled again."""
    now = time.time()
    with _JOBS_LOCK:
        for job_id, job in list(_JOBS.items()):
            if job.status != "running" and now - job.created_at > JOB_TTL_SEC:
                _JOBS.pop(job_id, None)
                _TASKS.pop(job_id, None)
                if _BY_ARTIST.get(job.artist_id) == job_id:
                    _BY_ARTIST.pop(job.artist_id, None)


def active_jobs() -> int:
    with _JOBS_LOCK:
        return sum(1 for j in _JOBS.values() if j.status == "running")


def job_for_artist(artist_id: int) -> Optional[ScanJob]:
    with _JOBS_LOCK:
        job_id = _BY_ARTIST.get(artist_id)
        job = _JOBS.get(job_id) if job_id else None
        return job if job and job.status == "running" else None


def get_job(job_id: str, actor_id: int) -> Optional[ScanJob]:
    """Look a job up. A job is visible to its own actor only."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job and job.actor_id == actor_id:
            return job
    return None


def start_artist_scan(artist_id: int, display_name: str, actor_id: int) -> Dict[str, Any]:
    """Register a job and launch it. Returns the initial snapshot."""
    _reap()
    with _JOBS_LOCK:
        duplicate = _BY_ARTIST.get(artist_id)
        if duplicate and _JOBS.get(duplicate) and _JOBS[duplicate].status == "running":
            raise ScanBusy(f"Сканирование «{display_name}» уже выполняется")
        if sum(1 for j in _JOBS.values() if j.status == "running") >= MAX_CONCURRENT_SCANS:
            raise ScanBusy(
                f"Одновременно выполняется слишком много сканирований "
                f"(максимум {MAX_CONCURRENT_SCANS})"
            )

        job = ScanJob(
            job_id=f"scan-{uuid.uuid4().hex[:12]}",
            artist_id=artist_id,
            display_name=display_name,
            actor_id=actor_id,
        )
        _JOBS[job.job_id] = job
        _BY_ARTIST[artist_id] = job.job_id

    task = asyncio.create_task(_run(job))
    # Keep a strong reference: a bare Task can be garbage-collected mid-run.
    _TASKS[job.job_id] = task
    task.add_done_callback(lambda t, jid=job.job_id: _TASKS.pop(jid, None))
    return job.snapshot()


class ScanBusy(Exception):
    """HTTP-status-carrying error for a job that cannot be started."""

    def __init__(self, detail: str, status: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status = status


class ScanNotFound(Exception):
    def __init__(self, detail: str = "Задача сканирования не найдена"):
        super().__init__(detail)
        self.detail = detail
        self.status = 404


async def _run(job: ScanJob) -> None:
    """Execute the scan, updating `job.progress` as it goes."""
    from app.core.database import SessionLocal

    def report(stage: str, message: str, current: Optional[int] = None, total: Optional[int] = None):
        if current is not None:
            job.progress["current"] = current
        if total is not None:
            job.progress["total"] = total
        job.progress["stage"] = stage
        job.progress["message"] = message

    db = SessionLocal()
    try:
        result = await scan_artist_in_db(db, job.artist_id, report)
        job.report = result
        job.status = "completed"
    except Exception as exc:  # a failed job must be visible, not silent
        logger.warning("Artist scan failed for #%s: %s", job.artist_id, type(exc).__name__)
        job.status = "failed"
        job.error = str(exc) or "Не удалось выполнить сканирование"
        job.progress["stage"] = "failed"
        job.progress["message"] = job.error
    finally:
        db.close()


async def scan_artist_in_db(db, artist_id: int, report: Callable[..., None]):
    """Refresh one artist from Last.fm: biography, MBID, artwork.

    Two provider quirks shape this:

    * `artist.getInfo` no longer serves artist imagery — every artist gets the
      same placeholder asset, which used to be written into the row (and, since
      the cover URL is derived from it, collapsed every scanned artist onto one
      picture). `lastfm_service` now filters it out, so the artist cover is
      taken from the first album that still has real cover art.
    * the wiki summary is often just the «Read more on Last.fm» stub, which is
      no longer stored as a biography.

    Albums that have no cover of their own are filled in as well, capped by
    `MAX_ALBUMS_PER_SCAN`. Nothing is downgraded: an existing cover is only
    replaced when it is missing or was itself a placeholder.
    """
    if not lastfm_service.metadata_ready():
        raise ScanUnavailable("Last.fm metadata is not configured.")

    artist = library_service.get_artist(db, artist_id)
    if not artist:
        raise ScanMissing("Artist not found.")

    report("artist", f"Запрос данных «{artist.display_name}» из Last.fm…", 0, 0)
    # Last.fm is a blocking urllib call (8s timeout). This coroutine runs on the
    # shared event loop, so calling it inline froze every stream and API request
    # for the duration of the scan (PERF-001).
    info = await asyncio.to_thread(
        lastfm_service.get_artist_info, artist.display_name, force=True
    )
    if not info:
        raise ScanMissing("Artist was not found on Last.fm.")

    fields: list = []
    summary = (info.get("summary") or "").strip()
    if summary and summary != (artist.bio or "").strip():
        artist.bio = summary
        fields.append("bio")
    if info.get("mbid") and info["mbid"] != artist.mbid:
        artist.mbid = info["mbid"]
        fields.append("mbid")

    # ── Covers ───────────────────────────────────────────────────────────────
    albums = library_service.list_artist_albums(db, artist.id, limit=MAX_ALBUMS_PER_SCAN * 4)
    # Albums needing a cover first, so the bar reaches a real 100%.
    to_fetch = [
        album
        for album in albums
        if not album.custom_image_key
        and (not album.cover_url or lastfm_service.is_placeholder_image(album.cover_url))
    ][:MAX_ALBUMS_PER_SCAN]
    album_cover: Optional[str] = None

    for album in albums:
        if album.custom_image_key:
            continue  # a hand-picked cover always wins — never fetch over it
        existing = album.cover_url
        if existing and not lastfm_service.is_placeholder_image(existing):
            album_cover = album_cover or existing
            continue

    report("albums", "Поиск обложек альбомов…", 0, len(to_fetch))
    for index, album in enumerate(to_fetch, start=1):
        report("albums", f"Обложка альбома «{album.title}»…", index, len(to_fetch))
        cover = await _fetch_album_cover(artist.display_name, album.title)
        if cover:
            album.cover_url = cover
            album.enriched_at = utcnow()
            album.updated_at = utcnow()
            album_cover = album_cover or cover

    albums_updated = sum(
        1
        for album in to_fetch
        if album.cover_url and not lastfm_service.is_placeholder_image(album.cover_url)
    )

    provider_image = info.get("image_url")
    chosen = provider_image or album_cover
    current = artist.image_url
    if chosen and chosen != current and (not current or lastfm_service.is_placeholder_image(current)):
        artist.image_url = chosen
        fields.append("image_url")

    if artist.image_url and artist.image_url == provider_image:
        image_source = "lastfm"
    elif artist.image_url and artist.image_url == album_cover:
        image_source = "album"
    elif artist.image_url:
        image_source = "existing"
    else:
        image_source = None

    touched = bool(fields) or albums_updated > 0
    if touched:
        report("save", "Сохранение изменений…", 1, 1)
        artist.enriched_at = utcnow()
        artist.updated_at = utcnow()
        # An explicit scan is a curation action: stop automatic enrichment from
        # overwriting the wording afterwards.
        artist.locked = True
        db.commit()
        db.refresh(artist)
        # Cards resolve covers through the local disk cache, so the chosen
        # image has to be fetched once or the card would 404 on display.
        if "image_url" in fields and image_source == "lastfm":
            try:
                from app.services.cover_cache_service import cover_cache

                await cover_cache.fetch_and_cache(artist.image_url)
            except Exception:
                logger.debug("Cover cache warm failed for artist %s", artist.id, exc_info=True)

    report(
        "done",
        "Готово" if touched else "Новых данных у Last.fm нет",
        1,
        1,
    )
    return {
        "ok": True,
        "id": artist.id,
        "display_name": artist.display_name,
        "updated": touched,
        "fields": fields,
        "albums_scanned": len(to_fetch),
        "albums_updated": albums_updated,
        "albums_total": len(albums),
        "image_source": image_source,
        "bio": artist.bio,
        "image_url": artist.image_url,
        "mbid": artist.mbid,
        "tags": info.get("tags") or [],
        "similar": info.get("similar") or [],
    }


async def _fetch_album_cover(artist_name: str, album_title: str) -> Optional[str]:
    """Last.fm album artwork + a one-off warm of the local disk cache.

    Best-effort by design: a scan must not fail when Last.fm or the image host
    is unavailable. Returns None when Last.fm has no real artwork (the shared
    placeholder is filtered out by lastfm_service.pick_image).
    """
    info = await asyncio.to_thread(
        lastfm_service.get_album_info, artist_name, album_title, force=True
    )
    url = (info or {}).get("image_url")
    if not url:
        return None
    try:
        from app.services.cover_cache_service import cover_cache

        await cover_cache.fetch_and_cache(url)
    except Exception:
        logger.debug("Cover cache warm failed for album %r", album_title, exc_info=True)
    return url


class ScanUnavailable(Exception):
    def __init__(self, detail: str = "Last.fm metadata is not configured."):
        super().__init__(detail)
        self.detail = detail
        self.status = 503


class ScanMissing(Exception):
    def __init__(self, detail: str = "Artist was not found on Last.fm."):
        super().__init__(detail)
        self.detail = detail
        self.status = 404
