"""
Tests for T-Music Stream Service, Decryption Service, and Range Requests
"""
import sys
import os
import struct
import base64
import hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import HTTPException
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.fernet import Fernet

from app.services.stream_service import StreamService
from app.services.decryption_service import DecryptionService
from app.tg.manager import decrypt_session_string
from app.core.config import settings


def test_parse_range_header_full():
    # No range or invalid header defaults to full size
    start, end, length = StreamService.parse_range_header(None, 1000)
    assert (start, end, length) == (0, 999, 1000)

    start, end, length = StreamService.parse_range_header("invalid", 1000)
    assert (start, end, length) == (0, 999, 1000)


def test_parse_range_header_valid_ranges():
    # bytes=0-499
    start, end, length = StreamService.parse_range_header("bytes=0-499", 1000)
    assert (start, end, length) == (0, 499, 500)

    # bytes=500-
    start, end, length = StreamService.parse_range_header("bytes=500-", 1000)
    assert (start, end, length) == (500, 999, 500)

    # suffix range
    start, end, length = StreamService.parse_range_header("bytes=-200", 1000)
    assert (start, end, length) == (800, 999, 200)

    # single byte
    start, end, length = StreamService.parse_range_header("bytes=10-10", 1000)
    assert (start, end, length) == (10, 10, 1)


def test_parse_range_header_out_of_bounds():
    # Out of bounds raises 416
    with pytest.raises(HTTPException) as exc:
        StreamService.parse_range_header("bytes=1000-2000", 1000)
    assert exc.value.status_code == 416

    with pytest.raises(HTTPException) as exc:
        StreamService.parse_range_header("bytes=500-400", 1000)
    assert exc.value.status_code == 416


def test_parse_encryption_header():
    salt = b"A" * 16
    base_nonce = b"B" * 12
    block_size = 1048576
    header = salt + base_nonce + struct.pack("<I", block_size)
    assert len(header) == 32

    s, n, bs = DecryptionService.parse_header(header)
    assert s == salt
    assert n == base_nonce
    assert bs == block_size

    with pytest.raises(ValueError):
        DecryptionService.parse_header(b"short_header")


def test_encrypted_size_to_plain_size():
    block_size = 1048576
    tag_size = 16
    header_size = 32

    # Under header size has 0 plaintext bytes
    assert DecryptionService.encrypted_size_to_plain_size(20, block_size) == 0
    assert DecryptionService.encrypted_size_to_plain_size(32, block_size) == 0

    # Exactly 1 block of 1000 plaintext bytes:
    # Encrypted = 32 (header) + 1000 + 16 (tag) = 1048 bytes
    enc_size = header_size + 1000 + tag_size
    assert DecryptionService.encrypted_size_to_plain_size(enc_size, block_size) == 1000

    # Exactly 2 full blocks (each 1048576 plain + 16 tag)
    enc_2blocks = header_size + 2 * (block_size + tag_size)
    assert DecryptionService.encrypted_size_to_plain_size(enc_2blocks, block_size) == 2 * block_size


@pytest.mark.asyncio
async def test_decryption_service_stream_roundtrip():
    # Create synthetic encrypted payload with Master Key
    master_key = settings.get_master_keys()[0]
    salt = os.urandom(16)
    base_nonce = os.urandom(12)
    block_size = 65536  # small block size for testing

    # Derive file key
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"tgcloud_file_v1"
    )
    file_key = hkdf.derive(master_key)
    aesgcm = AESGCM(file_key)

    plaintext = b"Hello, T-Music Decryption Stream! " * 5000  # ~170 KB, 3 blocks
    total_len = len(plaintext)

    # Build encrypted bytes: 32-byte header + blocks with additive nonce
    encrypted_data = bytearray(salt + base_nonce + struct.pack("<I", block_size))
    plain_offset = 0
    block_idx = 0
    while plain_offset < total_len:
        plain_chunk = plaintext[plain_offset:plain_offset + block_size]
        base_int = int.from_bytes(base_nonce, "big")
        block_nonce = ((base_int + block_idx) % (1 << 96)).to_bytes(12, "big")
        ciphertext = aesgcm.encrypt(block_nonce, plain_chunk, None)
        encrypted_data.extend(ciphertext)
        plain_offset += len(plain_chunk)
        block_idx += 1

    # Async generator helper
    async def raw_gen():
        # Feed in chunks of 8192 bytes
        chunk_size = 8192
        for i in range(0, len(encrypted_data), chunk_size):
            yield bytes(encrypted_data[i:i + chunk_size])

    # 1. Decrypt from start (skip_header=False)
    dec_stream = DecryptionService.decrypt_stream(
        raw_stream=raw_gen(),
        plain_limit=total_len,
        skip_header=False
    )
    decrypted_full = bytearray()
    async for chunk in dec_stream:
        decrypted_full.extend(chunk)

    assert bytes(decrypted_full) == plaintext

    # 2. Test Range Seek into block 1 with offset
    seek_start = 70000  # in block 1 (block_size is 65536)
    seek_len = 10000
    expected_slice = plaintext[seek_start:seek_start + seek_len]

    start_block = seek_start // block_size
    offset_in_block = seek_start % block_size
    enc_bs = block_size + 16
    raw_enc_offset = 32 + start_block * enc_bs

    async def raw_seek_gen():
        raw_slice = bytes(encrypted_data[raw_enc_offset:])
        for i in range(0, len(raw_slice), 8192):
            yield raw_slice[i:i + 8192]

    dec_seek_stream = DecryptionService.decrypt_stream(
        raw_stream=raw_seek_gen(),
        salt=salt,
        base_nonce=base_nonce,
        block_size=block_size,
        start_block=start_block,
        plain_offset_in_first_block=offset_in_block,
        plain_limit=seek_len,
        skip_header=True
    )
    decrypted_seek = bytearray()
    async for chunk in dec_seek_stream:
        decrypted_seek.extend(chunk)

    assert bytes(decrypted_seek) == expected_slice


def test_decrypt_session_string():
    secret = "my_super_secret_session_key_12345"
    key = hashlib.sha256(secret.encode()).digest()
    fernet = Fernet(base64.urlsafe_b64encode(key))

    original = "1BAA...pyrogram_session_data_xyz..."
    encrypted = fernet.encrypt(original.encode()).decode()

    # Successful decryption
    dec = decrypt_session_string(encrypted, secret)
    assert dec == original

    # Raw string returns as-is
    assert decrypt_session_string(original, secret) == original
    assert decrypt_session_string(None, secret) is None


@pytest.mark.asyncio
async def test_stream_get_only_enqueues_transcode_for_playback_requests(monkeypatch):
    """A buffering Range prefetch must never enqueue a transcode job.

    Only a real playback GET (no X-Prefetch marker) may trigger the
    PLAYING-priority job for the requested track; otherwise every queue start
    mass-transcodes the whole upcoming window.
    """
    from types import SimpleNamespace

    import app.api.stream_router as sr

    track = SimpleNamespace(id=42, name="song.m4a", mime_type="audio/mp4")
    enqueued = []

    monkeypatch.setattr(sr.stream_service, "get_track_or_404", lambda db, file_id: track)
    monkeypatch.setattr(sr, "needs_transcoding", lambda *a, **k: True)
    monkeypatch.setattr(sr.transcoding_service, "get_result", lambda file_id: None)
    monkeypatch.setattr(sr.stream_service, "get_track_size", lambda t: 1000)
    monkeypatch.setattr(
        sr.transcoding_service,
        "request_transcode",
        lambda t, priority=0: enqueued.append(priority),
    )

    # Playback request → exactly one job at PLAYING priority (0)
    await sr.stream_audio_file(42, SimpleNamespace(headers={}), db=None, user=None)
    assert enqueued == [0]
    enqueued.clear()

    # Buffering/prefetch request → nothing may be enqueued
    await sr.stream_audio_file(
        42,
        SimpleNamespace(headers={"X-Prefetch": "1", "Range": "bytes=0-99"}),
        db=None,
        user=None,
    )
    assert enqueued == []
