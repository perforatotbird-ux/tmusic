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
            return
        self._started = False
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
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
            # If playing track is requested as prefetch, upgrade priority
            if priority == PRIORITY_PLAYING and existing.priority == PRIORITY_PREFETCH:
                self._queue.promote(track.id, PRIORITY_PLAYING)
                existing.priority = PRIORITY_PLAYING
                logger.info("Upgraded priority for file %d (now playing)", track.id)
            return existing.status

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
        )
        self._jobs[track.id] = job

        if not is_transcode:
            # File doesn't need transcoding — update cache and notify
            self._set_cached(track.id, cache_key, ready=False, not_needed=True)
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
            if self._queue.full():
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

    def get_status(self, file_id: int) -> TranscodeStatus:
        """Check current transcoding status for a file."""
        # Check in-process job state first
        job = self._jobs.get(file_id)
        if job:
            if job.status == TranscodeStatus.READY:
                return TranscodeStatus.READY
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

    def get_result(self, file_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed transcode result — cached path, size, duration."""
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
                priority, ts, file_id = await self._queue.get()
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
                    }, job.requester_user_ids)
                    continue

                job.status = TranscodeStatus.PROCESSING
                job.started_at = time.time()

                self._notify({
                    "event": "transcode_started",
                    "file_id": file_id,
                    "file_name": job.file_name,
                    "priority": "high" if priority == PRIORITY_PLAYING else "prefetch",
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
                        "error": "transcode_failed", "timestamp": time.time(),
                    }, job.requester_user_ids)
            finally:
                self._queue.task_done()

        logger.info("Worker %s stopped", name)

    # ─── Core transcoding logic ──────────────────────────────────────────────

    async def _download_track_bytes(self, job: TranscodeJob) -> bytes:
        """
        Download the entire raw audio file from Telegram as plaintext bytes.
        Handles both encrypted and unencrypted files.
        """
        from sqlalchemy import select
        from app.core.database import SessionLocal
        from app.repositories.file_repository import FileRepository

        db = SessionLocal()
        try:
            track = FileRepository.get_by_id(db, job.file_id)
            if not track:
                raise ValueError(f"Track {job.file_id} not found in DB")

            # Build source_data dict for fast_streamer (same as stream_service does)
            source_data = {
                "account_map": dict(track.account_map or {}) if track.account_map else {},
                "msg_ids": str(track.msg_ids) if track.msg_ids else None,
                "tg_message_id": track.tg_message_id,
                "is_encrypted": bool(track.is_encrypted),
                "size": track.size,
                "encryption_salt": track.encryption_salt,
                "id": track.id,
                "name": track.name,
            }

            if not track.is_encrypted:
                # Unencrypted: just stream all bytes from Telegram
                raw_bytes = bytearray()
                stream_gen = fast_streamer.stream_file(source=source_data, offset=0)
                try:
                    async for chunk in stream_gen:
                        raw_bytes.extend(chunk)
                finally:
                    if hasattr(stream_gen, "aclose"):
                        await stream_gen.aclose()
                return bytes(raw_bytes)

            # Encrypted: stream + decrypt
            # Read the 32-byte header first
            header_gen = fast_streamer.stream_file(source=source_data, offset=0, limit=32)
            header_bytes = b""
            try:
                async for c in header_gen:
                    header_bytes += c
                    if len(header_bytes) >= 32:
                        break
            finally:
                if hasattr(header_gen, "aclose"):
                    await header_gen.aclose()

            if len(header_bytes) < 32:
                raise RuntimeError("Failed to read encryption header")

            salt, base_nonce, block_size = DecryptionService.parse_header(header_bytes[:32])

            # Stream encrypted data starting after the header
            total_plain = stream_service_get_plain_size(track)
            enc_offset = 32
            raw_stream = fast_streamer.stream_file(source=source_data, offset=enc_offset)

            dec_stream = DecryptionService.decrypt_stream(
                raw_stream=raw_stream,
                salt=salt,
                base_nonce=base_nonce,
                block_size=block_size,
                skip_header=True,
                plain_limit=total_plain,
            )

            plain_bytes = bytearray()
            try:
                async for chunk in dec_stream:
                    plain_bytes.extend(chunk)
            finally:
                if hasattr(dec_stream, "aclose"):
                    await dec_stream.aclose()
                if hasattr(raw_stream, "aclose"):
                    await raw_stream.aclose()

            return bytes(plain_bytes)

        finally:
            db.close()

    async def _transcode_track(self, job: TranscodeJob):
        """Download track bytes, transcode to MP3, cache to disk, and notify."""
        output_path = settings.TRANSCODE_CACHE_DIR / f"{job.cache_key}.mp3"

        # Check binaries
        if not ensure_ffmpeg_binaries():
            raise RuntimeError("ffmpeg binaries not available")

        # Download raw bytes
        logger.info("Downloading raw audio for file %d (%s)", job.file_id, job.file_name)
        self._notify({
            "event": "transcode_downloading",
            "file_id": job.file_id,
            "file_name": job.file_name,
            "timestamp": time.time(),
        }, job.requester_user_ids)

        raw_bytes = await self._download_track_bytes(job)
        logger.info("Downloaded %d bytes for file %d", len(raw_bytes), job.file_id)

        # Transcode to MP3 320kbps CBR using ffmpeg subprocess
        logger.info("Transcoding file %d to MP3 320kbps...", job.file_id)

        # Write raw bytes to a temp file: MP4/M4A inputs with the moov atom at
        # the end require seeking, which is impossible on a non-seekable pipe.
        ext = safe_src_ext(job.file_name)
        src_tmp = settings.TRANSCODE_CACHE_DIR / f"{job.cache_key}.src.{ext}"
        try:
            with open(src_tmp, "wb") as f:
                f.write(raw_bytes)

            cmd = [
                str(FFMPEG_EXE),
                "-y",
                "-i", str(src_tmp),
                "-vn",
                "-c:a", "libmp3lame",
                "-b:a", f"{settings.TRANSCODE_BITRATE_KBPS}k",
                "-cutoff", "20000",
                "-map_metadata", "0",
                "-f", "mp3",
                "pipe:1",
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_data, stderr_data = await communicate_with_cleanup(
                proc,
                timeout=600,  # 10 minute timeout for large files
            )
        finally:
            try:
                src_tmp.unlink(missing_ok=True)
            except OSError:
                pass

        if proc.returncode != 0:
            stderr_text = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
            # Show the tail of stderr — the actual error is at the end,
            # after the version banner and stream info.
            raise RuntimeError(f"ffmpeg failed (code {proc.returncode}): {stderr_text[-800:]}")

        # Validate output: reject empty/garbage MP3s (e.g. header-only output)
        if len(stdout_data) < 1024:
            stderr_text = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
            raise RuntimeError(
                f"ffmpeg produced invalid MP3 ({len(stdout_data)} bytes): {stderr_text[-800:]}"
            )

        # Write transcoded MP3 to disk cache
        with open(output_path, "wb") as f:
            f.write(stdout_data)

        mp3_size = output_path.stat().st_size
        job.mp3_size = mp3_size
        job.finished_at = time.time()
        job.status = TranscodeStatus.READY

        # Get duration using ffprobe
        duration = get_mp3_duration(str(output_path))
        job.duration = duration

        # Write manifest
        write_cache_manifest(job.cache_key, {
            "status": "ready",
            "cache_key": job.cache_key,
            "file_id": job.file_id,
            "file_name": job.file_name,
            "file_size": job.file_size,
            "mp3_size": mp3_size,
            "duration": duration,
            "bitrate_kbps": settings.TRANSCODE_BITRATE_KBPS,
            "created_at": time.time(),
        })

        # Clean up cache if it's growing too large
        self._cleanup_cache()

        # Notify only users who requested this job.
        self._notify({
            "event": "transcode_complete",
            "file_id": job.file_id,
            "file_name": job.file_name,
            "cache_key": job.cache_key,
            "mp3_size": mp3_size,
            "duration": duration,
            "timestamp": time.time(),
        }, job.requester_user_ids)

        logger.info("Transcode complete for file %d: %d bytes MP3 (%.1fs)", job.file_id, mp3_size, duration or 0)

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

        # Search manifest files for this file_id
        for manifest_file in settings.TRANSCODE_CACHE_DIR.glob("*.meta.json"):
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                if manifest.get("file_id") == file_id and manifest.get("status") == "ready":
                    cached_path = get_cached_transcode_path(manifest["cache_key"])
                    if cached_path:
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
            except (OSError, json.JSONDecodeError):
                continue
        return None

    def _cleanup_cache(self):
        """Evict oldest cached files when cache exceeds max size."""
        try:
            cache_dir = settings.TRANSCODE_CACHE_DIR
            total_size = sum(f.stat().st_size for f in cache_dir.glob("*.mp3") if f.is_file())
            if total_size <= settings.TRANSCODE_CACHE_MAX_BYTES:
                return

            # Sort MP3 files by mtime (oldest first)
            mp3_files = sorted(
                cache_dir.glob("*.mp3"),
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
