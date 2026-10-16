"""Disk-backed transcode regressions. Synthetic media and isolated filesystem only."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.services import transcoding_service as module
from app.services.stream_service import StreamService
from app.services.transcoding_service import TranscodingService, TranscodeJob, TranscodeStatus


def job():
    return TranscodeJob(42, 'Long.m4a', 6, False, 'audio/mp4', 0, 'd'*32)


@pytest.mark.asyncio
async def test_explicit_mp3_never_falls_back_to_raw(monkeypatch):
    service = StreamService()
    monkeypatch.setattr(module.transcoding_service, 'get_result', lambda _: None)
    monkeypatch.setattr(service, 'get_plain_track_size', Mock(side_effect=AssertionError('must not fetch raw')))
    with pytest.raises(HTTPException) as error:
        await service.resolve_representation(SimpleNamespace(id=42, name='Long.m4a', mime_type='audio/mp4'), require_transcoded=True)
    assert error.value.status_code == 409
    assert error.value.headers['Retry-After'] == '2'


@pytest.mark.asyncio
async def test_explicit_mp3_selects_prepared_size_and_mime(monkeypatch, tmp_path):
    output = tmp_path/'prepared.mp3'
    output.write_bytes(b'mp3'*1024)
    monkeypatch.setattr(module.transcoding_service, 'get_result', lambda _: {'cached_path': str(output)})
    selected = await StreamService().resolve_representation(SimpleNamespace(id=42, name='Long.m4a', mime_type='audio/mp4'), require_transcoded=True)
    assert selected.cached_path == output
    assert selected.media_type == 'audio/mpeg'
    assert selected.size == output.stat().st_size


@pytest.mark.asyncio
@pytest.mark.parametrize('truncated', [False, True])
async def test_download_spools_chunks_and_rejects_partial_source(monkeypatch, tmp_path, truncated):
    from app.core import database
    from app.repositories.file_repository import FileRepository
    track = SimpleNamespace(id=42, name='Long.m4a', size=6, mime_type='audio/mp4', is_encrypted=False)
    class DB:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(database, 'SessionLocal', DB)
    monkeypatch.setattr(FileRepository, 'get_by_id', lambda db, file_id: track)
    closed = []
    async def chunks(**kwargs):
        try:
            yield b'ab'; yield b'cd'
            if not truncated: yield b'ef'
        finally:
            closed.append(True)
    monkeypatch.setattr(module.fast_streamer, 'stream_file', chunks)
    service = TranscodingService()
    path = tmp_path/'input.m4a'
    path.write_bytes(b'')
    if truncated:
        with pytest.raises(RuntimeError, match='incomplete'):
            await service._download_track_to_file(job(), path)
    else:
        assert await service._download_track_to_file(job(), path) == 6
        assert path.read_bytes() == b'abcdef'
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['ok', 'failure', 'timeout'])
async def test_output_published_only_after_success_and_temps_removed(monkeypatch, tmp_path, outcome):
    monkeypatch.setattr(settings, 'TRANSCODE_CACHE_DIR', tmp_path)
    monkeypatch.setattr(module, 'ensure_ffmpeg_binaries', lambda: True)
    monkeypatch.setattr(module, 'get_mp3_duration', lambda _: 1800.0)
    service = TranscodingService()
    async def download(current, path):
        await service._disk_call(path.write_bytes, b'abcdef')
        return 6
    monkeypatch.setattr(service, '_download_track_to_file', download)
    class Proc:
        returncode = 1 if outcome == 'failure' else 0
    async def spawn(*args, **kwargs):
        assert args[-1].endswith('.part.mp3')
        assert kwargs['stdout'] == asyncio.subprocess.DEVNULL
        Path(args[-1]).write_bytes(b'ID3'+b'x'*2048)
        assert not (tmp_path/(job().cache_key+'.mp3')).exists()
        return Proc()
    async def communicate(proc, timeout):
        assert timeout == settings.TRANSCODE_TIMEOUT_SEC
        if outcome == 'timeout': raise asyncio.TimeoutError()
        return None, b'synthetic error'
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(module, 'communicate_with_cleanup', communicate)
    current = job()
    if outcome == 'ok':
        await service._transcode_track(current)
        assert current.status == TranscodeStatus.READY
        assert current.duration == 1800.0
        assert module.get_cached_transcode_path(current.cache_key)
    else:
        with pytest.raises((RuntimeError, asyncio.TimeoutError)):
            await service._transcode_track(current)
        assert current.status != TranscodeStatus.READY
        assert not module.get_cached_transcode_path(current.cache_key)
    assert not list(tmp_path.glob('*.src.*'))
    assert not list(tmp_path.glob('*.part.mp3'))


def test_cache_cleanup_preserves_active_output_and_ignores_partial_files(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'TRANSCODE_CACHE_DIR', tmp_path)
    monkeypatch.setattr(settings, 'TRANSCODE_CACHE_MAX_BYTES', 100)
    active = tmp_path/'active.mp3'; old = tmp_path/'old.mp3'; partial = tmp_path/'work.part.mp3'
    for path in [active, old, partial]: path.write_bytes(b'x'*200)
    TranscodingService()._cleanup_cache('active')
    assert active.exists() and partial.exists() and not old.exists()


def test_evicted_ready_job_is_retryable_failure_instead_of_false_ready(monkeypatch, tmp_path):
    # Изолированный каталог кеша: с 2026-10-16 статус дополнительно проверяется
    # по индексу манифестов, поэтому тест не должен зависеть от содержимого
    # реального cache/music_transcoded.
    monkeypatch.setattr(settings, 'TRANSCODE_CACHE_DIR', tmp_path)
    service = TranscodingService()
    current = job()
    current.status = TranscodeStatus.READY
    service._jobs[current.file_id] = current
    monkeypatch.setattr(module, 'get_cached_transcode_path', lambda _: None)
    assert service.get_status(current.file_id) == TranscodeStatus.FAILED
    assert not service.has_active_job(current.file_id)


def test_explicit_mp3_route_preserves_auth_dependency_range_and_mime(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import stream_router
    from app.api.dependencies import get_stream_user
    from app.core.database import get_db
    output = tmp_path/'prepared.mp3'; output.write_bytes(b'ID3'+b'x'*2048)
    track = SimpleNamespace(id=42, name='Long.m4a', size=6, mime_type='audio/mp4', updated_at=None)
    monkeypatch.setattr(stream_router.stream_service, 'get_track_or_404', lambda db, file_id: track)
    monkeypatch.setattr(module.transcoding_service, 'get_result', lambda _: {'cached_path': str(output)})
    db = Mock()
    app = FastAPI(); app.include_router(stream_router.router)
    app.dependency_overrides[get_stream_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        response = client.get('/api/stream/42?representation=mp3', headers={'Range':'bytes=0-99'})
        assert response.status_code == 206
        assert response.headers['content-type'].startswith('audio/mpeg')
        assert response.headers['content-range'] == f'bytes 0-99/{output.stat().st_size}'
        assert response.headers['cache-control'] == 'private, no-store'
        assert len(response.content) == 100
        assert client.get('/api/stream/42?representation=other').status_code == 422
        monkeypatch.setattr(module.transcoding_service, 'get_result', lambda _: None)
        response = client.get('/api/stream/42?representation=mp3')
        assert response.status_code == 409
        assert response.headers['retry-after'] == '2'
    db.close.assert_called()


@pytest.mark.asyncio
async def test_download_encrypted_track_resolves_exact_plain_size_and_does_not_truncate(monkeypatch, tmp_path):
    from app.core import database
    from app.repositories.file_repository import FileRepository
    from app.services.stream_service import stream_service
    from app.services.decryption_service import DecryptionService
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import struct

    block_size = 512 * 1024
    key = b'K' * 32; salt = b'S' * 16; nonce = b'N' * 12
    plaintext = b'M' * (2 * block_size + 1234)
    file_key = DecryptionService.derive_file_key(key, salt)
    aes = AESGCM(file_key)

    header = bytearray(salt + nonce + struct.pack('<I', block_size))
    blocks_data = bytearray()
    num_blocks = (len(plaintext) + block_size - 1) // block_size
    for i in range(num_blocks):
        b_nonce = ((int.from_bytes(nonce, 'big') + i) % (1 << 96)).to_bytes(12, 'big')
        chunk = plaintext[i * block_size : (i + 1) * block_size]
        blocks_data.extend(aes.encrypt(b_nonce, chunk, None))

    encrypted_stream_bytes = bytes(header + blocks_data)
    # The real DB stores track.size == len(plaintext)
    track = SimpleNamespace(
        id=99, name='encrypted.m4a', size=len(plaintext), mime_type='audio/mp4',
        is_encrypted=True, account_map={'1': {'msg_ids': '10'}}, msg_ids='10'
    )
    class DB:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(database, 'SessionLocal', DB)
    monkeypatch.setattr(FileRepository, 'get_by_id', lambda db, file_id: track)
    monkeypatch.setattr(type(settings), 'get_master_keys', lambda self: [key])

    # fast_streamer mock
    async def mock_stream_file(source, offset=0, limit=None):
        data = encrypted_stream_bytes[offset : offset + limit if limit is not None else None]
        for i in range(0, len(data), 65536):
            yield bytes(data[i : i + 65536])

    monkeypatch.setattr(module.fast_streamer, 'stream_file', mock_stream_file)
    monkeypatch.setattr(module.fast_streamer, 'get_source_media_size', lambda src: len(encrypted_stream_bytes))
    monkeypatch.setattr('app.services.stream_service.fast_streamer.stream_file', mock_stream_file)
    monkeypatch.setattr('app.services.stream_service.fast_streamer.get_source_media_size', lambda src: len(encrypted_stream_bytes))

    service = TranscodingService()
    path = tmp_path / 'decrypted_input.m4a'
    path.write_bytes(b'')

    test_job = TranscodeJob(99, 'encrypted.m4a', len(plaintext), True, 'audio/mp4', 0, 'e' * 32)
    downloaded = await service._download_track_to_file(test_job, path)
    assert downloaded == len(plaintext)
    assert path.stat().st_size == len(plaintext)
    assert path.read_bytes() == plaintext

