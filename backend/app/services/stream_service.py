"""
T-Music Stream Service
Coordinates audio track resolution, HTTP Range requests (RFC 7233),
AES-256-GCM block decryption with multi-key trial, header caching, and RAM buffer.
"""
import logging
from collections import OrderedDict
from typing import Optional, Tuple, AsyncGenerator, Dict, Any
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.vfs import VFile
from app.repositories.file_repository import FileRepository
from app.services.ram_cache_service import ram_cache
from app.services.decryption_service import DecryptionService
from app.tg.downloader import fast_streamer

logger = logging.getLogger(__name__)


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

    @staticmethod
    def get_track_size(track: VFile) -> int:
        """Returns effective plaintext audio size."""
        if not track.size or track.size <= 0:
            return 0
        if track.is_encrypted:
            return DecryptionService.encrypted_size_to_plain_size(track.size)
        return track.size

    @staticmethod
    def parse_range_header(range_header: Optional[str], total_size: int) -> Tuple[int, int, int]:
        """
        Parses Range: bytes=start-end header.
        Returns (start, end, content_length).
        """
        if not range_header or not range_header.startswith("bytes="):
            return 0, max(0, total_size - 1), total_size

        try:
            byte_range = range_header.replace("bytes=", "").split("-")
            start = int(byte_range[0]) if byte_range[0] else 0
            end = int(byte_range[1]) if len(byte_range) > 1 and byte_range[1] else total_size - 1

            if start >= total_size or end >= total_size or start > end:
                raise HTTPException(
                    status_code=416,
                    detail="Requested Range Not Satisfiable",
                    headers={"Content-Range": f"bytes */{total_size}"}
                )

            return start, end, (end - start + 1)
        except HTTPException:
            raise
        except Exception:
            return 0, max(0, total_size - 1), total_size

    async def get_stream_generator(
        self,
        track: VFile,
        start: int,
        length: int
    ) -> AsyncGenerator[bytes, None]:
        """
        Generates decrypted plaintext byte stream from RAM cache or Telegram MTProto.
        Detached-safe: snapshots all needed VFile attributes before yielding.
        """
        file_id = track.id
        file_name = track.name
        is_encrypted = bool(track.is_encrypted)
        total_plain_size = self.get_track_size(track)

        # Snapshot track data to avoid SQLAlchemy detached session errors
        source_data: Dict[str, Any] = {
            "account_map": dict(track.account_map or {}) if track.account_map else {},
            "msg_ids": str(track.msg_ids) if track.msg_ids else None,
            "tg_message_id": track.tg_message_id,
            "is_encrypted": is_encrypted,
            "size": track.size,
            "encryption_salt": track.encryption_salt,
            "id": file_id,
            "name": file_name,
        }

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

                # Buffer entire track to RAM cache if full stream requested
                if length >= total_plain_size and total_plain_size > 0:
                    buf = bytearray()
                    async for chunk in dec_stream:
                        buf.extend(chunk)
                        yield chunk
                    await ram_cache.put(file_id, bytes(buf))
                else:
                    async for chunk in dec_stream:
                        yield chunk
            else:
                # Seeking into encrypted track (RFC 7233 Range Seek)
                # Check header cache first
                cached_hdr = self._header_cache.get(file_id)
                if cached_hdr:
                    self._header_cache.move_to_end(file_id)
                    salt, base_nonce, block_size = cached_hdr
                else:
                    header_bytes = b""
                    h_gen = fast_streamer.stream_file(source=source_data, offset=0, limit=32)
                    try:
                        async for c in h_gen:
                            header_bytes += c
                            if len(header_bytes) >= 32:
                                break
                    finally:
                        if hasattr(h_gen, "aclose"):
                            await h_gen.aclose()

                    if len(header_bytes) < 32:
                        raise RuntimeError("Failed to read encryption header for seek.")

                    salt, base_nonce, block_size = DecryptionService.parse_header(header_bytes[:32])
                    self._header_cache[file_id] = (salt, base_nonce, block_size)
                    self._header_cache.move_to_end(file_id)
                    while len(self._header_cache) > self.HEADER_CACHE_MAX_ITEMS:
                        self._header_cache.popitem(last=False)

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
                async for chunk in dec_stream:
                    yield chunk

        except Exception as e:
            logger.error("Streaming error for track %d (%s): %s", file_id, file_name, e)
            raise HTTPException(status_code=503, detail="Streaming service temporarily unavailable.")


stream_service = StreamService()
