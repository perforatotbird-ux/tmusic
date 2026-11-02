"""
T-Music Transcoding Service
Orchestrates background transcoding of non-browser-compatible audio formats
(m4a, flac, ogg, opus, wma, aac, mp4) to MP3 320kbps CBR for browser playback.

Design:
- Background asyncio workers pull from a priority queue
- Disk cache stores transcoded MP3s with manifest metadata
- SSE (Server-Sent Events) broadcasts real-time status to frontend clients
- Integrates with existing fast_streamer + DecryptionService for Telegram downloads
"""
import asyncio
import heapq
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List, Any, Set
from fastapi import HTTPException
from app.core.rate_limiter import limiter

from app.core.config import settings
from app.utils.ffmpeg_transcoder import (
    communicate_with_cleanup,
    TranscodeStatus,
    needs_transcoding,
    make_cache_key,
    get_cached_transcode_path,
    write_cache_manifest,
    ensure_ffmpeg_binaries,
    get_mp3_duration,
    FFMPEG_EXE,
)
from app.models.vfs import VFile
from app.tg.downloader import fast_streamer
from app.services.decryption_service import DecryptionService

logger = logging.getLogger("tmusic.transcoder")

PRIORITY_PLAYING = 0
PRIORITY_PREFETCH = 1
MAX_QUEUED_TRANSCODES = 100
MAX_USER_ACTIVE_TRANSCODES = 50
# Prefetch must never be able to consume the whole queue: playback has to get a
# slot even when a user has just opened several large collections (PERF-003).
PLAYBACK_RESERVE_SLOTS = 10
MAX_COMPLETED_JOBS = 1000
COMPLETED_JOB_TTL = 3600
JOB_SWEEP_INTERVAL = 60

# 2026-10-16: сколько живёт in-memory индекс манифестов кеша. Раньше статус
# каждого трека искался полным обходом каталога с json-разбором всех манифестов:
# на папке из 454 треков открытие сборника занимало 15.5 с (и всё это время
# событийный цикл был занят, поэтому «висел» и уже идущий транскодинг).
MANIFEST_INDEX_TTL = 30.0
# Сборка списка манифестов — это одна операция чтения каталога; повторять её
# на каждый get_status нельзя, но и раз в 30 с её цена (~единицы мс) незаметна.
PRUNE_MIN_INTERVAL = 1.0
# Троттлинг prune нужен только когда таблица задач реально большая: до этого
# размера обход дешевле самой проверки времени (и тесты полагаются на то, что
# устаревшая терминальная задача исчезает из _jobs сразу).
PRUNE_SMALL_TABLE = 64


class TranscodePriorityQueue(asyncio.PriorityQueue):
    """Single-event-loop heap supporting atomic in-place promotion."""
    def _init(self, maxsize):
        super()._init(maxsize)
        self._entries = self._queue

    def promote(self, file_id: int, priority: int) -> bool:
        for i, (old_priority, timestamp, queued_id) in enumerate(self._entries):
            if queued_id == file_id:
                if priority < old_priority:
                    self._entries[i] = (priority, timestamp, queued_id)
                    heapq.heapify(self._entries)
                return True
        return False  # Already acquired by a worker: do not enqueue again.


@dataclass
class TranscodeJob:
    """Tracks the state of a single transcoding job."""
    file_id: int
    file_name: str
    file_size: int
    is_encrypted: bool
    mime_type: Optional[str]
    priority: int
    cache_key: str
    status: TranscodeStatus = TranscodeStatus.PENDING
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    mp3_size: Optional[int] = None
    duration: Optional[float] = None
    error: Optional[str] = None
    requester_user_ids: Set[int] = field(default_factory=set)
    # True для задач, поставленных предзагрузкой коллекции (открытие сборника).
    # Клиент по этому флагу не показывает тост на каждый фоновый трек, но
    # по-прежнему получает transcode_complete для быстрого старта плеера.
    background: bool = False


_SRC_EXT_RE = re.compile(r"[a-z0-9]{1,10}")


def safe_src_ext(file_name: Optional[str]) -> str:
    """Sanitized temp-file extension for a track name coming from the DB.

    2026-09-29: the raw extension used to flow straight into the temp-file
    path (TRANSCODE_CACHE_DIR / f"{key}.src.{ext}"), so a crafted track name
    like "x.mp3/../../evil" escaped the cache directory. Anything outside
    [a-z0-9]{1,10} falls back to "audio" (ffmpeg sniffs the format anyway).
    """
    raw = (file_name.rsplit(".", 1)[-1] if file_name and "." in file_name else "audio").lower()
    return raw if _SRC_EXT_RE.fullmatch(raw or "") else "audio"


class TranscodingService:
    """Singleton service managing background audio transcoding, caching, and SSE notifications."""

    def __init__(self):
        self._queue: asyncio.PriorityQueue = None  # (priority, ts, file_id, track_id)
        self._jobs: Dict[int, TranscodeJob] = {}
        # Queues are isolated by authenticated user. A global queue leaked
        # filenames, file ids and backend errors between users.
        self._sse_clients: Dict[int, List[asyncio.Queue]] = {}
        self._workers: List[asyncio.Task] = []
        self._started: bool = False
        self._lock = asyncio.Lock()
        # file_id -> манифест готового MP3 на диске. Индекс строится одним
        # проходом по каталогу и живёт MANIFEST_INDEX_TTL; без него каждый
        # get_status/get_result обходил каталог целиком (см. MANIFEST_INDEX_TTL).
        self._disk_index: Dict[int, Dict[str, Any]] = {}
        self._disk_index_built_at: float = 0.0
        self._last_prune: float = 0.0

    def _prune_jobs(self):
        """Bound only terminal history; never evict pending/processing ownership."""
        now = time.time()
        # get_status() зовётся на каждый трек списка, а история задач может
        # доходить до MAX_COMPLETED_JOBS — без троттлинга это лишние сотни
        # тысяч итераций на одно открытие сборника. Но троттлинг включается
        # только на большой таблице, чтобы обычный путь не менял поведение.
        if len(self._jobs) > PRUNE_SMALL_TABLE and now - self._last_prune < PRUNE_MIN_INTERVAL:
            return
        self._last_prune = now
        terminal = []
        for file_id, job in list(self._jobs.items()):
            if job.status in (TranscodeStatus.PENDING, TranscodeStatus.PROCESSING):
                continue
            if job.finished_at is None:
                job.finished_at = now
            if now - job.finished_at >= COMPLETED_JOB_TTL:
                self._jobs.pop(file_id, None)
            else:
                terminal.append((job.finished_at, file_id))
        for _, file_id in sorted(terminal)[:max(0, len(terminal) - MAX_COMPLETED_JOBS)]:
            self._jobs.pop(file_id, None)

    # ─── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self, num_workers: int = None):
        """Spawn background worker tasks. Called from app lifespan."""
        if self._started:
            return
        self._queue = TranscodePriorityQueue(maxsize=MAX_QUEUED_TRANSCODES)
        num_workers = num_workers or settings.TRANSCODE_WORKERS
        self._started = True
        for i in range(num_workers):
            task = asyncio.create_task(self._worker(f"transcode-worker-{i}"))
            self._workers.append(task)
        logger.info("TranscodingService started with %d workers", num_workers)

    async def shutdown(self):
        """Cancel all background workers. Called from app lifespan."""
        if not self._started:
            self._jobs.clear()
            return
        self._started = False
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._jobs.clear()
        if self._queue is not None:
            while not self._queue.empty():
                self._queue.get_nowait()
                self._queue.task_done()
        # Notify clients of shutdown
        self._notify({
            "event": "service_shutdown",
            "timestamp": time.time(),
        })
        logger.info("TranscodingService shut down")

    # ─── Public API ────────────────────────────────────────────────────────────

    def request_transcode(
        self,
        track: VFile,
        priority: int = PRIORITY_PREFETCH,
        requester_user_id: Optional[int] = None,
        background: bool = False,
    ) -> TranscodeStatus:
        """Enqueue a track for transcoding. Returns current status (may already be cached)."""
        status = self.get_status(track.id)

        if status == TranscodeStatus.READY:
            return status

        if not self._started:
            logger.warning("TranscodingService not started — cannot enqueue file %d", track.id)
            return TranscodeStatus.FAILED

        active = sum(
            requester_user_id in job.requester_user_ids
            and job.status in (TranscodeStatus.PENDING, TranscodeStatus.PROCESSING)
            for job in self._jobs.values()
        ) if requester_user_id is not None else 0
        existing = self._jobs.get(track.id)
        already_subscribed = existing and requester_user_id in existing.requester_user_ids
        if active >= MAX_USER_ACTIVE_TRANSCODES and not already_subscribed:
            raise HTTPException(status_code=429, detail="Transcoding quota exceeded.")

        # Check if already in queue (don't duplicate)
        if existing and existing.status in (TranscodeStatus.PENDING, TranscodeStatus.PROCESSING):
            if requester_user_id is not None:
                existing.requester_user_ids.add(requester_user_id)
            # Фоновая предзагрузка не должна превращать уже видимую пользователю
            # задачу в «тихую»: флаг только включается, но не выключается.
            if background:
                existing.background = True
            # If playing track is requested as prefetch, upgrade priority
            if priority == PRIORITY_PLAYING and existing.priority == PRIORITY_PREFETCH:
                self._queue.promote(track.id, PRIORITY_PLAYING)
                existing.priority = PRIORITY_PLAYING
                logger.info("Upgraded priority for file %d (now playing)", track.id)
            return existing.status

        if self._queue.qsize() >= MAX_QUEUED_TRANSCODES - PLAYBACK_RESERVE_SLOTS and priority != PRIORITY_PLAYING:
            raise HTTPException(status_code=429, detail="Transcoding prefetch budget is full.")
        if self._queue.full():
            raise HTTPException(status_code=429, detail="Transcoding queue is full.")
        if requester_user_id is not None and limiter.is_rate_limited(
            f"transcodejobs:{requester_user_id}", 60, 60
        ):
            raise HTTPException(status_code=429, detail="Too many transcoding jobs.")

        cache_key = make_cache_key(
            file_id=track.id,
            file_name=track.name,
            file_size=track.size or 0,
            file_mtime=track.updated_at.timestamp() if track.updated_at else time.time(),
        )

        is_transcode = needs_transcoding(track.name, track.mime_type)

        job = TranscodeJob(
            file_id=track.id,
            file_name=track.name,
            file_size=track.size or 0,
            is_encrypted=bool(track.is_encrypted),
            mime_type=track.mime_type,
            priority=priority,
            cache_key=cache_key,
            requester_user_ids={requester_user_id} if requester_user_id is not None else set(),
            background=background,
        )
        self._jobs[track.id] = job

        if not is_transcode:
            # File doesn't need transcoding — update cache and notify
            job.status = TranscodeStatus.NOT_NEEDED
            job.finished_at = time.time()
            self._prune_jobs()
            return TranscodeStatus.NOT_NEEDED

        # Enqueue
        ts = time.time()
        self._queue.put_nowait((priority, ts, track.id))

        # Notify clients of pending status
        self._notify({
            "event": "transcode_queued",
            "file_id": track.id,
            "file_name": track.name,
            "priority": "high" if priority == PRIORITY_PLAYING else "prefetch",
            "background": background,
            "timestamp": ts,
        }, job.requester_user_ids)

        logger.info("Queued file %d for transcoding (priority=%d)", track.id, priority)
        return TranscodeStatus.PENDING

    def prefetch_next(self, track_ids: List[int], requester_user_id: Optional[int] = None) -> int:
        """Enqueue multiple tracks for background prefetch. Returns count enqueued."""
        enqueued = 0
        for file_id in track_ids:
            if file_id not in self._jobs or self._jobs[file_id].status in (
                TranscodeStatus.PENDING,
                TranscodeStatus.PROCESSING,
            ):
                continue
            # Only prefetch if file needs transcoding (will be checked in worker)
            if self._queue.qsize() >= MAX_QUEUED_TRANSCODES - PLAYBACK_RESERVE_SLOTS:
                break
            self._queue.put_nowait((PRIORITY_PREFETCH, time.time(), file_id))
            enqueued += 1
            self._notify({
                "event": "transcode_prefetch",
                "file_id": file_id,
                "timestamp": time.time(),
            }, {requester_user_id} if requester_user_id is not None else set())
        if enqueued:
            logger.info("Prefetched %d tracks for transcoding", enqueued)
        return enqueued

    def prefetch_collection(
        self,
        tracks: List[Any],
        requester_user_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> int:
        """Поставить в фоновую очередь транскодинг треков открытого сборника.

        Вызывается из обработчиков, отдающих список треков папки/плейлиста.
        Список отдаётся пользователю сразу, транскодинг идёт в фоне отдельными
        воркерами — поэтому открытие сборника больше не ждёт ни скачивания, ни
        ffmpeg. Никогда не бросает исключение: переполнение очереди, квоты и
        rate-limit обрывают залп молча, но не ломают ответ с треками.

        Порядок треков сохраняется (natural sort из БД), поэтому в первую
        очередь в фон уходят первые треки — те, что пользователь нажмёт раньше.
        """
        if not self._started or not settings.TRANSCODE_ENABLED or not settings.TRANSCODE_PREFETCH_ON_OPEN:
            return 0
        if limit is None:
            limit = settings.TRANSCODE_PREFETCH_ON_OPEN_MAX

        enqueued = 0
        try:
            for track in tracks:
                if enqueued >= limit:
                    break
                if self._queue.qsize() >= MAX_QUEUED_TRANSCODES - PLAYBACK_RESERVE_SLOTS:
                    logger.info(
                        "Transcode prefetch budget full — collection prefetch stopped after %d tracks",
                        enqueued,
                    )
                    break
                # Строка без имени не может быть треком (колонка NOT NULL), но
                # одна битая запись не должна обрывать залп по всей коллекции.
                name = getattr(track, "name", None)
                if not name:
                    continue
                if not needs_transcoding(name, getattr(track, "mime_type", None)):
                    continue
                try:
                    # Уже готовое (на диске) и уже поставленное не дублируем.
                    if self.get_status(track.id) == TranscodeStatus.READY:
                        continue
                    if self.has_active_job(track.id):
                        continue
                    status = self.request_transcode(
                        track,
                        priority=PRIORITY_PREFETCH,
                        requester_user_id=requester_user_id,
                        background=True,
                    )
                except HTTPException:
                    # Квота пользователя / лимит запросов / очередь — это не
                    # ошибка открытия сборника, просто прекращаем залп.
                    break
                if status in (TranscodeStatus.PENDING, TranscodeStatus.PROCESSING):
                    enqueued += 1
        except Exception:
            logger.warning("Collection prefetch failed after %d tracks", enqueued, exc_info=True)
        if enqueued:
            logger.info("Queued %d tracks for background transcode (collection open)", enqueued)
        return enqueued

    def get_status(self, file_id: int) -> TranscodeStatus:
        """Check current transcoding status for a file."""
        self._prune_jobs()
        # Check in-process job state first
        job = self._jobs.get(file_id)
        if job:
            if job.status == TranscodeStatus.READY:
                if get_cached_transcode_path(job.cache_key):
                    return TranscodeStatus.READY
                job.status = TranscodeStatus.FAILED
                job.error = "cache_evicted"
                return TranscodeStatus.FAILED
            if job.status == TranscodeStatus.PROCESSING:
                return TranscodeStatus.PROCESSING
            if job.status in (TranscodeStatus.PENDING, TranscodeStatus.FAILED):
                # Fall through to disk check — pending might have been cached
                pass
            else:
                return job.status

        # Check disk cache
        cached = self._check_disk_cache(file_id)
        if cached:
            return TranscodeStatus.READY

        return job.status if job and job.status == TranscodeStatus.FAILED else TranscodeStatus.PENDING

    def has_active_job(self, file_id: int) -> bool:
        job = self._jobs.get(file_id)
        return bool(job and job.status in (TranscodeStatus.PENDING, TranscodeStatus.PROCESSING))

    def get_result(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed transcode result — cached path, size, duration."""
        self._prune_jobs()
        job = self._jobs.get(file_id)
        if job and job.status == TranscodeStatus.READY:
            return {
                "cache_key": job.cache_key,
                "mp3_size": job.mp3_size,
                "duration": job.duration,
                "cached_path": get_cached_transcode_path(job.cache_key),
            }
        # Check disk cache
        cached = self._check_disk_cache(file_id)
        if cached:
            return cached
        return None

    # ─── SSE Notification ──────────────────────────────────────────────────────

    def subscribe(self, user_id: int, max_connections: int = 3) -> asyncio.Queue:
        """Create a user-scoped SSE queue with a per-user connection cap."""
        user_queues = self._sse_clients.setdefault(user_id, [])
        if len(user_queues) >= max_connections:
            raise ValueError("too_many_sse_connections")
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        user_queues.append(q)
        logger.debug(
            "SSE client subscribed (user=%d, user_total=%d, total=%d)",
            user_id,
            len(user_queues),
            sum(len(queues) for queues in self._sse_clients.values()),
        )
        return q

    def unsubscribe(self, user_id: int, q: asyncio.Queue):
        """Remove a user-scoped SSE client."""
        user_queues = self._sse_clients.get(user_id, [])
        if q in user_queues:
            user_queues.remove(q)
        if not user_queues:
            self._sse_clients.pop(user_id, None)
        logger.debug(
            "SSE client unsubscribed (user=%d, total=%d)",
            user_id,
            sum(len(queues) for queues in self._sse_clients.values()),
        )

    def _notify(self, event: Dict[str, Any], user_ids: Optional[Set[int]] = None):
        """Send an event only to its requesters; None is an explicit broadcast."""
        queues = (
            [q for qs in self._sse_clients.values() for q in qs]
            if user_ids is None
            else [q for user_id in user_ids for q in self._sse_clients.get(user_id, [])]
        )
        for client_q in queues:
            try:
                client_q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Client fell behind — drop notification

    # ─── Background worker ───────────────────────────────────────────────────

    async def _worker(self, name: str):
        """Background worker that processes transcoding jobs from the queue."""
        logger.info("Worker %s started", name)
        while self._started:
            try:
                priority, ts, file_id = await asyncio.wait_for(self._queue.get(), JOB_SWEEP_INTERVAL)
            except asyncio.TimeoutError:
                self._prune_jobs()
                continue
            except asyncio.CancelledError:
                break

            job = None
            try:
                # Get or create the job
                job = self._jobs.get(file_id)
                if job is None:
                    # Prefetch job — create from DB lookup
                    from app.core.database import SessionLocal
                    from app.repositories.file_repository import FileRepository
                    from app.utils.ffmpeg_transcoder import needs_transcoding

                    db = SessionLocal()
                    try:
                        track = FileRepository.get_by_id(db, file_id)
                        if not track:
                            continue

                        if not needs_transcoding(track.name, track.mime_type):
                            continue

                        cache_key = make_cache_key(
                            file_id=track.id,
                            file_name=track.name,
                            file_size=track.size or 0,
                            file_mtime=track.updated_at.timestamp() if track.updated_at else time.time(),
                        )
                        job = TranscodeJob(
                            file_id=track.id,
                            file_name=track.name,
                            file_size=track.size or 0,
                            is_encrypted=bool(track.is_encrypted),
                            mime_type=track.mime_type,
                            priority=priority,
                            cache_key=cache_key,
                        )
                        self._jobs[file_id] = job
                    finally:
                        db.close()

                # Double-check the file still needs transcoding
                # (it might have been cached between enqueue and processing)
                already_cached = get_cached_transcode_path(job.cache_key)
                if already_cached:
                    job.status = TranscodeStatus.READY
                    job.mp3_size = Path(already_cached).stat().st_size
                    job.finished_at = time.time()
                    self._notify({
                        "event": "transcode_complete",
                        "file_id": file_id,
                        "cache_key": job.cache_key,
                        "mp3_size": job.mp3_size,
                        "background": job.background,
                    }, job.requester_user_ids)
                    continue

                job.status = TranscodeStatus.PROCESSING
                job.started_at = time.time()

                self._notify({
                    "event": "transcode_started",
                    "file_id": file_id,
                    "file_name": job.file_name,
                    "priority": "high" if priority == PRIORITY_PLAYING else "prefetch",
                    "background": job.background,
                    "timestamp": time.time(),
                }, job.requester_user_ids)

                await self._transcode_track(job)
            except asyncio.CancelledError:
                if job is not None:
                    job.status = TranscodeStatus.FAILED
                    job.error = "service_shutdown"
                raise
            except Exception:
                logger.exception("Transcoding job failed for file %d", file_id)
                if job is not None:
                    job.status = TranscodeStatus.FAILED
                    job.error = "transcode_failed"
                    job.finished_at = time.time()
                    self._notify({
                        "event": "transcode_failed", "file_id": file_id,
                        "error": "transcode_failed", "background": job.background,
                        "timestamp": time.time(),
                    }, job.requester_user_ids)
            finally:
                self._queue.task_done()
                self._prune_jobs()

        logger.info("Worker %s stopped", name)

    # ─── Core transcoding logic ──────────────────────────────────────────────

    @staticmethod
    async def _disk_call(func, *args):
        """Finish an in-flight filesystem call before cancellation removes paths."""
        task = asyncio.create_task(asyncio.to_thread(func, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
            task.result()
            raise

    @staticmethod
    def _append_file(path: Path, data: bytes) -> None:
        with path.open("ab") as target:
            target.write(data)

    async def _download_track_to_file(self, job: TranscodeJob, path: Path) -> int:
        """Spool decrypted chunks to a seekable file; never retain the whole input."""
        from app.core.database import SessionLocal
        from app.repositories.file_repository import FileRepository
        from app.services.stream_service import stream_service
        with SessionLocal() as db:
            track = FileRepository.get_by_id(db, job.file_id)
            if not track:
                raise ValueError(f"Track {job.file_id} not found in DB")
            track = stream_service.snapshot_track(track)
        source = stream_service._source_snapshot(track)
        if track.is_encrypted:
            salt, nonce, block_size = await stream_service._get_encryption_header(source)
            expected = await stream_service.get_plain_track_size(track)
            raw = fast_streamer.stream_file(source=source, offset=32)
            stream = DecryptionService.decrypt_stream(
                raw_stream=raw, salt=salt, base_nonce=nonce, block_size=block_size,
                skip_header=True, plain_limit=expected,
            )
        else:
            expected = track.size or 0
            raw = fast_streamer.stream_file(source=source, offset=0)
            stream = raw
        received = 0
        try:
            async for chunk in stream:
                received += len(chunk)
                if expected and received > expected:
                    raise RuntimeError("Downloaded audio exceeds its expected size")
                await self._disk_call(self._append_file, path, chunk)
            if not received or (expected and received != expected):
                raise RuntimeError("Downloaded audio is incomplete")
            return received
        finally:
            try:
                if hasattr(stream, "aclose"):
                    await stream.aclose()
            finally:
                if stream is not raw and hasattr(raw, "aclose"):
                    await raw.aclose()

    async def _transcode_track(self, job: TranscodeJob):
        """Download track bytes, transcode to MP3, cache to disk, and notify."""
        output_path = settings.TRANSCODE_CACHE_DIR / f"{job.cache_key}.mp3"

        # Check binaries
        if not ensure_ffmpeg_binaries():
            raise RuntimeError("ffmpeg binaries not available")

        # M4A requires a seekable input (moov may live at the end of the file).
        # Input and output are disk-backed; only individual download chunks use RAM.
        ext = safe_src_ext(job.file_name)
        src_tmp = settings.TRANSCODE_CACHE_DIR / f"{job.cache_key}.src.{ext}"
        output_tmp = settings.TRANSCODE_CACHE_DIR / f"{job.cache_key}.part.mp3"
        logger.info("Downloading raw audio for file %d (%s)", job.file_id, job.file_name)
        self._notify({
            "event": "transcode_downloading", "file_id": job.file_id,
            "file_name": job.file_name, "background": job.background,
            "timestamp": time.time(),
        }, job.requester_user_ids)
        try:
            await self._disk_call(src_tmp.write_bytes, b"")
            downloaded = await asyncio.wait_for(
                self._download_track_to_file(job, src_tmp),
                timeout=settings.TRANSCODE_DOWNLOAD_TIMEOUT_SEC,
            )
            logger.info("Downloaded %d bytes for file %d", downloaded, job.file_id)
            cmd = [
                str(FFMPEG_EXE), "-y", "-nostats", "-loglevel", "error",
                "-i", str(src_tmp), "-vn", "-c:a", "libmp3lame",
                "-b:a", f"{settings.TRANSCODE_BITRATE_KBPS}k",
                "-cutoff", "20000", "-map_metadata", "0", "-f", "mp3", str(output_tmp),
            ]
            logger.info("Transcoding file %d to MP3...", job.file_id)
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr_data = await communicate_with_cleanup(
                proc, timeout=settings.TRANSCODE_TIMEOUT_SEC,
            )
            if proc.returncode != 0:
                detail = (stderr_data or b"").decode("utf-8", errors="replace")[-800:]
                raise RuntimeError(f"ffmpeg failed (code {proc.returncode}): {detail}")
            if not output_tmp.is_file() or output_tmp.stat().st_size < 1024:
                raise RuntimeError("ffmpeg produced invalid MP3")
            # Publish only a complete file; the manifest gates cache discovery.
            await self._disk_call(output_tmp.replace, output_path)
        finally:
            await self._disk_call(src_tmp.unlink, True)
            await self._disk_call(output_tmp.unlink, True)

        mp3_size = output_path.stat().st_size
        job.mp3_size = mp3_size
        job.finished_at = time.time()

        # Get duration using ffprobe in worker thread
        duration = await asyncio.to_thread(get_mp3_duration, str(output_path))
        job.duration = duration

        # Write manifest in worker thread
        manifest_payload = {
            "status": "ready",
            "cache_key": job.cache_key,
            "file_id": job.file_id,
            "file_name": job.file_name,
            "file_size": job.file_size,
            "mp3_size": mp3_size,
            "duration": duration,
            "bitrate_kbps": settings.TRANSCODE_BITRATE_KBPS,
            "created_at": time.time(),
        }
        await self._disk_call(write_cache_manifest, job.cache_key, manifest_payload)
        if not get_cached_transcode_path(job.cache_key):
            raise RuntimeError("Transcode cache manifest could not be published")
        job.status = TranscodeStatus.READY
        # Свежий манифест попадает в индекс сразу — иначе до истечения TTL
        # следующий запрос снова сканировал бы каталог.
        self._disk_index[job.file_id] = manifest_payload

        # Clean up cache in worker thread if it's growing too large
        await asyncio.to_thread(self._cleanup_cache, job.cache_key)

        # Notify only users who requested this job.
        self._notify({
            "event": "transcode_complete",
            "file_id": job.file_id,
            "file_name": job.file_name,
            "cache_key": job.cache_key,
            "mp3_size": mp3_size,
            "duration": duration,
            "background": job.background,
            "timestamp": time.time(),
        }, job.requester_user_ids)

        logger.info("Transcode complete for file %d: %d bytes MP3 (%.1fs)", job.file_id, mp3_size, duration or 0)

    @staticmethod
    def _cached_mp3_path(cache_key: str) -> Optional[str]:
        """Быстрая проверка готового MP3 по ключу из индекса манифестов.

        `get_cached_transcode_path` дополнительно читает и парсит манифест —
        для записи из индекса это лишняя работа: манифест уже разобран и
        проверен на `status == ready`. Остаётся тот же guard на мусорные
        (< 1 KiB) файлы от неудачных прогонов ffmpeg.
        """
        path = settings.TRANSCODE_CACHE_DIR / f"{cache_key}.mp3"
        try:
            if path.is_file() and path.stat().st_size >= 1024:
                return str(path)
        except OSError:
            pass
        return None

    def _refresh_disk_index(self, *, force: bool = False) -> Dict[int, Dict[str, Any]]:
        """Один проход по каталогу кеша вместо обхода на каждый трек.

        Раньше `_check_disk_cache` на каждый вызов читал и парсил ВСЕ
        `*.meta.json` в `TRANSCODE_CACHE_DIR`. Открытие сборника вызывает его
        дважды на трек, поэтому папка из 454 требующих транскодинга треков
        стоила ~70k чтений файлов и 15.5 с (замер 2026-10-16) прямо в
        обработчике запроса. Теперь каталог сканируется не чаще, чем раз в
        MANIFEST_INDEX_TTL, а поиск идёт по словарю.
        """
        now = time.time()
        if not force and self._disk_index_built_at and now - self._disk_index_built_at < MANIFEST_INDEX_TTL:
            return self._disk_index

        index: Dict[int, Dict[str, Any]] = {}
        try:
            manifest_files = list(settings.TRANSCODE_CACHE_DIR.glob("*.meta.json"))
        except OSError:
            manifest_files = []
        for manifest_file in manifest_files:
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("status") != "ready":
                continue
            file_id = manifest.get("file_id")
            cache_key = manifest.get("cache_key")
            if not isinstance(file_id, int) or not isinstance(cache_key, str) or not cache_key:
                continue
            # Один file_id может иметь несколько ключей (файл перезалили):
            # берём самый свежий манифест, а не первый по порядку обхода.
            previous = index.get(file_id)
            if previous is None or (manifest.get("created_at") or 0) >= (previous.get("created_at") or 0):
                index[file_id] = manifest
        self._disk_index = index
        self._disk_index_built_at = time.time()
        return index

    def _check_disk_cache(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Check if a transcoded file exists in the disk cache for this file_id."""
        # Look through jobs first for the cache_key
        job = self._jobs.get(file_id)
        if job and job.cache_key:
            cached_path = get_cached_transcode_path(job.cache_key)
            if cached_path:
                return {
                    "cache_key": job.cache_key,
                    "cached_path": cached_path,
                    "mp3_size": Path(cached_path).stat().st_size,
                }

        manifest = self._refresh_disk_index().get(file_id)
        if not manifest:
            return None
        cached_path = self._cached_mp3_path(manifest["cache_key"])
        if not cached_path:
            # Индекс устарел (файл вытеснен/удалён): убираем запись, чтобы
            # следующий вызов не проверял её повторно.
            self._disk_index.pop(file_id, None)
            return None
        if job:
            job.status = TranscodeStatus.READY
            job.cache_key = manifest["cache_key"]
            job.mp3_size = manifest.get("mp3_size")
            job.duration = manifest.get("duration")
        return {
            "cache_key": manifest["cache_key"],
            "cached_path": cached_path,
            "mp3_size": manifest.get("mp3_size"),
            "duration": manifest.get("duration"),
        }

    def _cleanup_cache(self, protected_key=None):
        """Evict oldest cached files when cache exceeds max size."""
        try:
            cache_dir = settings.TRANSCODE_CACHE_DIR
            total_size = sum(f.stat().st_size for f in cache_dir.glob("*.mp3") if f.is_file() and not f.name.endswith(".part.mp3") and ".src." not in f.name)
            if total_size <= settings.TRANSCODE_CACHE_MAX_BYTES:
                return

            # Sort MP3 files by mtime (oldest first)
            mp3_files = sorted(
                (f for f in cache_dir.glob("*.mp3") if not f.name.endswith(".part.mp3") and ".src." not in f.name and f.stem != protected_key),
                key=lambda f: f.stat().st_mtime
            )
            for f in mp3_files:
                if total_size <= settings.TRANSCODE_CACHE_MAX_BYTES:
                    break
                size = f.stat().st_size
                try:
                    f.unlink()
                    # Also remove manifest
                    manifest = cache_dir / f"{f.stem}.meta.json"
                    if manifest.exists():
                        manifest.unlink()
                    # Индекс не должен отдавать вытесненный файл как READY.
                    for file_id, entry in list(self._disk_index.items()):
                        if entry.get("cache_key") == f.stem:
                            self._disk_index.pop(file_id, None)
                    total_size -= size
                    logger.info("Evicted cached transcode: %s", f.name)
                except OSError:
                    pass
        except Exception as e:
            logger.warning("Cache cleanup failed: %s", e)


def stream_service_get_plain_size(track: VFile) -> int:
    """Calculate plaintext audio size for decryption (replicates stream_service logic)."""
    if not track.size or track.size <= 0:
        return 0
    if track.is_encrypted:
        from app.services.decryption_service import HEADER_SIZE, DEFAULT_BLOCK_SIZE
        payload = track.size - HEADER_SIZE
        enc_bs = DEFAULT_BLOCK_SIZE + 16  # TAG_SIZE
        num_full_blocks = payload // enc_bs
        remainder = payload % enc_bs
        last_plain = max(0, remainder - 16) if remainder > 0 else 0
        return num_full_blocks * DEFAULT_BLOCK_SIZE + last_plain
    return track.size


transcoding_service = TranscodingService()
