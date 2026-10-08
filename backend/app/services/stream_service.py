"""
T-Music Stream Service
Coordinates audio track resolution, HTTP Range requests (RFC 7233),
AES-256-GCM block decryption with multi-key trial, header caching, and RAM buffer.
"""
import logging
import asyncio
from contextlib import aclosing
import re
import urllib.parse
from dataclasses import dataclass
from types import SimpleNamespace
from collections import OrderedDict
from typing import Optional, Tuple, AsyncGenerator, Dict, Any
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
from sqlalchemy.orm import Session
from pathlib import Path

from app.models.vfs import VFile
from app.repositories.file_repository import FileRepository
from app.services.ram_cache_service import ram_cache
from app.services.decryption_service import DecryptionService
from app.tg.downloader import fast_streamer
from app.core.config import settings
from app.utils.ffmpeg_transcoder import needs_transcoding, TranscodeStatus

logger = logging.getLogger(__name__)


class CheckedStreamingResponse(StreamingResponse):
    """Close a prefetched source even if ASGI disconnects before body iteration."""
    def __init__(self, *args, source, **kwargs):
        self.source = source
        super().__init__(*args, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.source.aclose()


@dataclass(frozen=True)
class StreamRepresentation:
    size: int
    media_type: str
    cached_path: Optional[Path] = None


class StreamService:
    """Service coordinating audio stream delivery and decryption."""

    # Cap for the encryption-header cache: entries are tiny, but the key space
    # is unbounded (one per seeked track), so evict LRU beyond this.
    HEADER_CACHE_MAX_ITEMS = 2000

    def __init__(self):
        # Cache for encryption headers: file_id -> (salt, base_nonce, block_size)
        self._header_cache: OrderedDict[int, Tuple[bytes, bytes, int]] = OrderedDict()

    @staticmethod
    def get_track_or_404(db: Session, file_id: int) -> VFile:
        track = FileRepository.get_by_id(db, file_id)
        if not track:
            raise HTTPException(status_code=404, detail="Track not found.")
        return track

    @classmethod
    def snapshot_track(cls, track):
        """Copy scalar stream/job fields before releasing the read transaction."""
        return SimpleNamespace(**cls._source_snapshot(track),
                               mime_type=getattr(track, "mime_type", None),
                               updated_at=getattr(track, "updated_at", None))

    def get_track_size(self, track: VFile) -> Optional[int]:
        """Known plaintext size; encrypted cards stay unknown until header resolution."""
        if not track.size or track.size <= 0:
            return 0
        if getattr(track, "is_encrypted", False):
            header = self._header_cache.get(track.id)
            if header is None:
                return None  # Never silently assume a 1 MiB encrypted block.
            return DecryptionService.encrypted_size_to_plain_size(track.size, header[2])
        return track.size

    @staticmethod
    def _source_snapshot(track) -> Dict[str, Any]:
        return {
            "account_map": dict(getattr(track, "account_map", None) or {}),
            "msg_ids": getattr(track, "msg_ids", None),
            "tg_message_id": getattr(track, "tg_message_id", None),
            "is_encrypted": bool(getattr(track, "is_encrypted", False)),
            "size": getattr(track, "size", None),
            "encryption_salt": getattr(track, "encryption_salt", None),
            "id": track.id, "name": track.name,
        }

    async def _get_encryption_header(self, source_data):
        file_id = source_data["id"]
        cached = self._header_cache.get(file_id)
        if cached is not None:
            self._header_cache.move_to_end(file_id)
            return cached
        raw = fast_streamer.stream_file(source=source_data, offset=0, limit=32)
        header_bytes = bytearray()
        try:
            async for chunk in raw:
                header_bytes.extend(chunk)
                if len(header_bytes) >= 32:
                    break
        finally:
            if hasattr(raw, "aclose"):
                await raw.aclose()
        if len(header_bytes) < 32:
            raise RuntimeError("Failed to read encryption header")
        header = DecryptionService.parse_header(bytes(header_bytes[:32]))
        self._header_cache[file_id] = header
        self._header_cache.move_to_end(file_id)
        while len(self._header_cache) > self.HEADER_CACHE_MAX_ITEMS:
            self._header_cache.popitem(last=False)
        return header

    async def get_plain_track_size(self, track) -> int:
        size = self.get_track_size(track)
        if size is not None:
            return size
        ram_data = await ram_cache.get(track.id)
        if ram_data is not None:
            return len(ram_data)
        try:
            header = await self._get_encryption_header(self._source_snapshot(track))
            return DecryptionService.encrypted_size_to_plain_size(track.size, header[2])
        except Exception:
            logger.exception("Failed to resolve encryption header for track %d", track.id)
            raise HTTPException(status_code=503, detail="Streaming service temporarily unavailable.")

    async def resolve_representation(self, track, *, require_transcoded=False) -> StreamRepresentation:
        from app.services.transcoding_service import transcoding_service
        if needs_transcoding(track.name, track.mime_type):
            result = transcoding_service.get_result(track.id)
            if result and result.get("cached_path"):
                path = Path(result["cached_path"])
                try:
                    size = path.stat().st_size
                    if path.is_file() and size > 0:
                        return StreamRepresentation(size, "audio/mpeg", path)
                except OSError:
                    pass  # Cache disappeared: select the original before headers.
        if require_transcoded and needs_transcoding(track.name, track.mime_type):
            raise HTTPException(status_code=409, detail="Transcoded audio is not ready.", headers={"Retry-After": "2"})
        return StreamRepresentation(await self.get_plain_track_size(track), track.mime_type or "audio/mpeg")

    async def build_stream_response(self, request, track, *, cache_control="private, max-age=3600", on_original=None, require_transcoded=False):
        """Select once: headers, Range, format and body describe the same representation."""
        representation = await self.resolve_representation(track, require_transcoded=require_transcoded)
        if representation.size <= 0:
            raise HTTPException(status_code=400, detail="Audio file has invalid size.")
        if representation.cached_path is None and on_original is not None:
            on_original()
        range_header = request.headers.get("Range")
        valid_range = bool(range_header and re.fullmatch(r"bytes=(?:[0-9]+-[0-9]*|-[0-9]+)", range_header.strip()))
        if valid_range and range_header.strip() == "bytes=-0":
            valid_range = False
        start, end, length = self.parse_range_header(range_header.strip() if valid_range else None, representation.size)
        filename = track.name
        if representation.cached_path is not None:
            filename = Path(filename).stem + ".mp3"
        headers = {
            "Content-Disposition": f"inline; filename*=UTF-8''{urllib.parse.quote(filename)}",
            "Accept-Ranges": "bytes", "Content-Length": str(length), "Cache-Control": cache_control,
        }
        if valid_range:
            headers["Content-Range"] = f"bytes {start}-{end}/{representation.size}"
        if representation.cached_path is not None:
            def file_chunks():
                with representation.cached_path.open("rb") as source:
                    source.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
            body = file_chunks()
        else:
            # A transcode completing later must not swap out the selected raw source.
            body = self.get_stream_generator(track, start, length, prefer_transcoded=False)
        async def threaded_body():
            try:
                async for chunk in iterate_in_threadpool(body):
                    yield chunk
            finally:
                await run_in_threadpool(body.close)
        iterator = body.__aiter__() if hasattr(body, "__aiter__") else threaded_body()
        async def first_nonempty():
            async for chunk in iterator:
                if chunk:
                    return chunk
            raise RuntimeError("Audio source ended before the first byte")
        try:
            first = await asyncio.wait_for(first_nonempty(), timeout=30)
            if len(first) > length:
                raise RuntimeError("Audio source exceeded the selected range")
        except BaseException as exc:
            await iterator.aclose()
            if not isinstance(exc, Exception):
                raise  # Cancellation/SystemExit is never converted to HTTP failure.
            logger.exception("Audio preflight failed; track_id=%d", track.id)
            raise HTTPException(status_code=503, detail="Streaming service temporarily unavailable.") from exc

        async def checked_body():
            remaining = length
            try:
                remaining -= len(first)
                yield first
                async for chunk in iterator:
                    if len(chunk) > remaining:
                        raise RuntimeError("Audio source exceeded the selected range")
                    remaining -= len(chunk)
                    yield chunk
                if remaining:
                    raise RuntimeError("Audio source ended before the selected range was complete")
            except Exception:
                logger.exception("Audio body interrupted; track_id=%d remaining_bytes=%d", track.id, remaining)
                raise  # Headers are sent: abort the stream, do not invent a new status.
            finally:
                await iterator.aclose()
        return CheckedStreamingResponse(checked_body(), source=iterator,
                                        status_code=206 if valid_range else 200,
                                        media_type=representation.media_type, headers=headers)

    @staticmethod
    def get_transcoded_mp3_size(track: VFile) -> Optional[int]:
        """Returns the size of a cached transcoded MP3 if available, else None."""
        from app.services.transcoding_service import transcoding_service
        result = transcoding_service.get_result(track.id)
        if result and result.get("mp3_size"):
            return result["mp3_size"]
        return None

    def build_track_responses(self, db: Session, vfiles: list) -> list:
        """TrackResponse list with plaintext size + best-known duration.

        VFS stores no probe metadata; duration comes from playback history
        (playback_positions), so freshly imported tracks show no length until
        they have been streamed once.
        """
        from app.schemas.stream import TrackResponse
        from app.services.playback_service import playback_service
        from app.services.transcoding_service import transcoding_service, TranscodeStatus

        durations = playback_service.durations_map(db, [vf.id for vf in vfiles])
        out = []
        for vf in vfiles:
            needs_transcode = needs_transcoding(vf.name, vf.mime_type)
            transcode_status = transcribing_status = None
            if needs_transcode:
                transcode_status = transcoding_service.get_status(vf.id)
                if transcode_status == TranscodeStatus.READY:
                    mp3_size = StreamService.get_transcoded_mp3_size(vf)
                    effective_size = mp3_size or self.get_track_size(vf)
                else:
                    effective_size = self.get_track_size(vf)
            else:
                effective_size = self.get_track_size(vf)

            out.append(
                TrackResponse(
                    id=vf.id,
                    name=vf.name,
                    size=effective_size,
                    mime_type=vf.mime_type or "audio/mpeg",
                    folder_id=vf.folder_id,
                    stream_url=f"/api/stream/{vf.id}",
                    created_at=vf.created_at,
                    duration=durations.get(vf.id),
                )
            )
        return out

    @staticmethod
    def parse_range_header(range_header: Optional[str], total_size: int) -> Tuple[int, int, int]:
        if total_size <= 0:
            return 0, -1, 0
        if not range_header or not range_header.startswith("bytes="):
            return 0, total_size - 1, total_size

        value = range_header[6:].strip()
        if "," in value or "-" not in value:
            return 0, total_size - 1, total_size
        start_raw, end_raw = value.split("-", 1)
        try:
            if not start_raw:
                suffix_length = int(end_raw)
                if suffix_length <= 0:
                    raise ValueError
                start = max(0, total_size - suffix_length)
                end = total_size - 1
            else:
                start = int(start_raw)
                end = int(end_raw) if end_raw else total_size - 1
        except ValueError:
            return 0, total_size - 1, total_size

        if start < 0 or start >= total_size or end < start:
            raise HTTPException(
                status_code=416,
                detail="Requested Range Not Satisfiable",
                headers={"Content-Range": f"bytes */{total_size}"}
            )
        end = min(end, total_size - 1)
        return start, end, end - start + 1

    async def get_stream_generator(
        self,
        track: VFile,
        start: int,
        length: int,
        *, prefer_transcoded: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        """
        Generates decrypted plaintext byte stream from RAM cache or Telegram MTProto.
        Detached-safe: snapshots all needed VFile attributes before yielding.
        If a transcoded MP3 is cached on disk, streams from that instead.
        """
        file_id = track.id
        file_name = track.name

        # ─── 0. Check if a transcoded MP3 is cached on disk ────────────────────
        from app.services.transcoding_service import transcoding_service

        if prefer_transcoded and needs_transcoding(file_name, track.mime_type):
            result = transcoding_service.get_result(file_id)
            if result and result.get("cached_path"):
                cached_path = result["cached_path"]
                cached_size = result.get("mp3_size", Path(cached_path).stat().st_size)
                end = start + length
                if length >= cached_size:
                    # Full file streaming — use file-level IO
                    with open(cached_path, "rb") as f:
                        if start > 0:
                            f.seek(start)
                        while True:
                            chunk = f.read(1024 * 1024)  # 1 MB chunks
                            if not chunk:
                                break
                            yield chunk
                    return
                else:
                    # Range request
                    with open(cached_path, "rb") as f:
                        f.seek(start)
                        remaining = min(length, cached_size - start)
                        while remaining > 0:
                            chunk = f.read(min(1024 * 1024, remaining))
                            if not chunk:
                                break
                            yield chunk
                            remaining -= len(chunk)
                    return

        # ─── 1. Check RAM cache (plaintext buffer) ────────────────────────────
        is_encrypted = bool(track.is_encrypted)
        total_plain_size = await self.get_plain_track_size(track)

        # Snapshot track data to avoid SQLAlchemy detached session errors
        source_data = self._source_snapshot(track)

        # 1. Check RAM cache (plaintext buffer)
        ram_data = await ram_cache.get(file_id)
        if ram_data:
            end = start + length
            yield ram_data[start:end]
            return

        # 2. Telegram stream
        try:
            if not is_encrypted:
                # Unencrypted track
                stream_gen = fast_streamer.stream_file(
                    source=source_data,
                    offset=start,
                    limit=length
                )
                async with aclosing(stream_gen):
                    async for chunk in stream_gen:
                        yield chunk
                return

            # --- Encrypted Track (AES-256-GCM) ---
            if start == 0:
                # Streaming from beginning: header is present at bytes 0..32
                raw_stream = fast_streamer.stream_file(source=source_data, offset=0)
                dec_stream = DecryptionService.decrypt_stream(
                    raw_stream=raw_stream,
                    plain_limit=length,
                    skip_header=False
                )

                async with aclosing(raw_stream), aclosing(dec_stream):
                    # Buffer entire track to RAM cache if full stream requested
                    if length >= total_plain_size and total_plain_size > 0:
                        async with ram_cache.reserve_buffer(total_plain_size) as cacheable:
                            buf = bytearray() if cacheable else None
                            received = 0
                            try:
                                async for chunk in dec_stream:
                                    received += len(chunk)
                                    if received > total_plain_size:
                                        raise RuntimeError("Decrypted audio source exceeded its size")
                                    if buf is not None:
                                        buf.extend(chunk)
                                    yield chunk
                                if received != total_plain_size:
                                    raise RuntimeError("Decrypted audio source was incomplete")
                                if buf is not None:
                                    await ram_cache.put(file_id, bytes(buf))
                            finally:
                                if buf is not None:
                                    buf.clear()
                    else:
                        async for chunk in dec_stream:
                            yield chunk
            else:
                # Seeking into encrypted track (RFC 7233 Range Seek)
                salt, base_nonce, block_size = await self._get_encryption_header(source_data)

                enc_bs = DecryptionService.encrypted_block_size(block_size)
                start_block = start // block_size
                plain_offset_in_first_block = start % block_size
                enc_offset = 32 + start_block * enc_bs

                raw_stream = fast_streamer.stream_file(source=source_data, offset=enc_offset)
                dec_stream = DecryptionService.decrypt_stream(
                    raw_stream=raw_stream,
                    salt=salt,
                    base_nonce=base_nonce,
                    block_size=block_size,
                    start_block=start_block,
                    plain_offset_in_first_block=plain_offset_in_first_block,
                    plain_limit=length,
                    skip_header=True
                )
                async with aclosing(raw_stream), aclosing(dec_stream):
                    async for chunk in dec_stream:
                        yield chunk

        except Exception:
            logger.exception("Audio source failed; track_id=%d", file_id)
            raise


stream_service = StreamService()
