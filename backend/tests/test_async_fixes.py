"""Async regressions: synthetic data, disposable SQLite, no external providers."""
import asyncio
import hashlib
import inspect
import sys
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.api import admin_router, admin_vfs_router, metadata_router, playlist_router
from app.api.dependencies import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.base import Base
from app.models import auth, music, playback, playlist, share, vfs
from app.services import backup_scheduler, cover_cache_service, transcoding_service, vfs_import_service as imports
from app.services.playlist_service import playlist_service
from app.utils.ffmpeg_transcoder import communicate_with_cleanup, TranscodeStatus
from test_vfs_import import _synthetic_dump


@pytest.fixture
def database():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        user = auth.AppUser(username='synthetic_admin', role='admin', is_active=True, password_hash='placeholder')
        other = auth.AppUser(username='synthetic_other', role='user', is_active=True, password_hash='placeholder')
        tracks = [vfs.VFile(name=f'Synthetic {i}.mp3', size=123, mime_type='audio/mpeg', is_trash=False) for i in range(3)]
        db.add_all([user, other, *tracks, vfs.TGAccount(id=7, name='Synthetic')]); db.commit()
        yield db, user, other, tracks, sessions
    engine.dispose()


@pytest.mark.parametrize('fn', [metadata_router.lyrics_lookup, metadata_router.artist_lookup,
    metadata_router.track_lookup, metadata_router.enrich_artist,
    admin_router.admin_run_backup, admin_router.admin_restore_backup])
def test_blocking_routes_use_fastapi_threadpool(fn):
    assert not inspect.iscoroutinefunction(fn)


@pytest.mark.asyncio
async def test_slow_provider_does_not_block_request_loop(monkeypatch):
    import httpx
    monkeypatch.setattr(metadata_router.lyrics_service, 'lyrics_enabled', lambda: True)
    def slow(**kwargs): time.sleep(0.08); return None
    monkeypatch.setattr(metadata_router.lyrics_service, 'get_lyrics', slow)
    app = FastAPI(); app.include_router(metadata_router.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
        pending = asyncio.create_task(client.get('/api/metadata/lyrics?track=Synthetic'))
        beats = 0
        while not pending.done():
            await asyncio.sleep(0.005); beats += 1
        assert (await pending).status_code == 200
        assert beats >= 5


def test_scheduler_session_is_created_inside_worker(monkeypatch):
    import threading
    main_thread = threading.get_ident(); threads = []
    session = Mock(); session.__enter__ = Mock(return_value=session); session.__exit__ = Mock(return_value=False)
    def session_factory(): threads.append(threading.get_ident()); return session
    monkeypatch.setattr(backup_scheduler, 'SessionLocal', session_factory)
    monkeypatch.setattr(backup_scheduler.admin_service, 'get_all_settings', lambda db: {'backup_enabled': True})
    monkeypatch.setattr(backup_scheduler.admin_service, 'list_backups', lambda db: [])
    monkeypatch.setattr(backup_scheduler.admin_service, 'run_pg_dump', lambda db, **kw: {'ok': True})
    async def run():
        import anyio
        assert await anyio.to_thread.run_sync(backup_scheduler._backup_tick, 0.0) > 0
    asyncio.run(run())
    assert threads and threads[0] != main_thread


class FakeProcess:
    def __init__(self):
        self.returncode = None; self.entered = asyncio.Event(); self.dead = asyncio.Event()
        self.killed = False; self.drained = False
    async def communicate(self):
        self.entered.set(); await self.dead.wait(); self.drained = True
        return b'output', b''
    def kill(self): self.killed = True; self.returncode = -9; self.dead.set()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['timeout', 'cancel'])
async def test_child_cleanup_precedes_propagating_failure(kind):
    proc = FakeProcess()
    task = asyncio.create_task(communicate_with_cleanup(proc, 0.01 if kind == 'timeout' else 600))
    await proc.entered.wait()
    if kind == 'cancel': task.cancel()
    with pytest.raises(TimeoutError if kind == 'timeout' else asyncio.CancelledError): await task
    assert proc.killed and proc.drained


@pytest.mark.asyncio
async def test_real_child_is_reaped_on_cancel():
    # Local interpreter only; a child with pipes demonstrates actual OS cleanup.
    proc = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(30)',
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    task = asyncio.create_task(communicate_with_cleanup(proc, 600))
    await asyncio.sleep(0.02); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert proc.returncode is not None
    assert await asyncio.wait_for(proc.wait(), 1) == proc.returncode


@pytest.mark.asyncio
async def test_cover_cancellation_is_per_caller_and_producer_is_observed(monkeypatch):
    service = cover_cache_service.CoverCacheService()
    monkeypatch.setattr(service, 'get_cached_item', lambda *a, **kw: None)
    monkeypatch.setattr(service, 'get_session', AsyncMock(return_value=None))
    monkeypatch.setattr(cover_cache_service, 'is_safe_host', AsyncMock(return_value=True))
    entered = asyncio.Event(); release = asyncio.Event()
    async def fetch(*args): entered.set(); await release.wait(); return None
    monkeypatch.setattr(cover_cache_service, '_fetch_safe', fetch)
    one = asyncio.create_task(service.fetch_and_cache('https://synthetic.example/cover'))
    two = asyncio.create_task(service.fetch_and_cache('https://synthetic.example/cover'))
    await entered.wait(); await asyncio.sleep(0); one.cancel()
    with pytest.raises(asyncio.CancelledError): await one
    assert not two.done()
    release.set(); assert await asyncio.wait_for(two, 1) == (None, None)
    await service.close(); assert not service._in_flight and not service._fetch_tasks


@pytest.mark.asyncio
async def test_cover_shutdown_resolves_waiters_and_closes_session(monkeypatch):
    service = cover_cache_service.CoverCacheService()
    monkeypatch.setattr(service, 'get_cached_item', lambda *a, **kw: None)
    monkeypatch.setattr(cover_cache_service, 'is_safe_host', AsyncMock(return_value=True))
    session = SimpleNamespace(closed=False, close=AsyncMock())
    service._session = session
    monkeypatch.setattr(service, 'get_session', AsyncMock(return_value=session))
    entered = asyncio.Event()
    async def fetch(*args): entered.set(); await asyncio.Event().wait()
    monkeypatch.setattr(cover_cache_service, '_fetch_safe', fetch)
    waiter = asyncio.create_task(service.fetch_and_cache('https://synthetic.example/cover'))
    await entered.wait(); await service.close()
    assert await waiter == (None, None)
    session.close.assert_awaited_once(); assert not service._fetch_tasks and not service._in_flight


@pytest.mark.asyncio
async def test_cover_shutdown_also_handles_not_yet_started_producer():
    service = cover_cache_service.CoverCacheService()
    future = asyncio.get_running_loop().create_future(); service._in_flight['key'] = future
    task = asyncio.create_task(service._do_fetch('https://synthetic.example', 'key', future))
    service._fetch_tasks.add(task)
    await service.close()
    assert future.result() == (None, None) and task.cancelled()


@pytest.mark.asyncio
async def test_worker_survives_metadata_failure_and_completes_queue(monkeypatch):
    service = transcoding_service.TranscodingService()
    monkeypatch.setattr('app.core.database.SessionLocal', Mock(return_value=Mock()))
    monkeypatch.setattr('app.repositories.file_repository.FileRepository.get_by_id', Mock(side_effect=RuntimeError('synthetic')))
    monkeypatch.setattr(transcoding_service, 'get_cached_transcode_path', lambda key: None)
    job = transcoding_service.TranscodeJob(2, 'Synthetic.flac', 123, False, 'audio/flac', 0, 'b'*32)
    service._jobs[2] = job
    async def transcode(target): target.status = TranscodeStatus.READY
    monkeypatch.setattr(service, '_transcode_track', transcode)
    service.start(num_workers=1)
    try:
        service._queue.put_nowait((0, 1, 1)); service._queue.put_nowait((0, 2, 2))
        await asyncio.wait_for(service._queue.join(), 1)
        assert job.status == TranscodeStatus.READY
        assert not service._workers[0].done()
    finally: await service.shutdown()


@pytest.mark.asyncio
async def test_worker_task_done_on_shutdown(monkeypatch):
    service = transcoding_service.TranscodingService()
    monkeypatch.setattr(transcoding_service, 'get_cached_transcode_path', lambda key: None)
    job = transcoding_service.TranscodeJob(1, 'Synthetic.flac', 123, False, 'audio/flac', 0, 'a'*32)
    service._jobs[1] = job; entered = asyncio.Event()
    async def transcode(target): entered.set(); await asyncio.Event().wait()
    monkeypatch.setattr(service, '_transcode_track', transcode)
    service.start(num_workers=1); service._queue.put_nowait((0, 1, 1)); await entered.wait()
    await service.shutdown(); await asyncio.wait_for(service._queue.join(), 1)
    assert job.status == TranscodeStatus.FAILED


def test_mirror_preserves_order_and_allows_explicit_clear(database):
    db, user, other, tracks, sessions = database
    ids = [tracks[2].id, tracks[0].id]
    result = playlist_service.mirror(db, user.id, '  Synthetic  ', ids)
    assert [row.file_id for row, _ in playlist_service.playlist_tracks(db, result.id)] == ids
    assert [row.position for row, _ in playlist_service.playlist_tracks(db, result.id)] == [1, 2]
    mirrored = playlist_service.mirror(db, user.id, 'synthetic', [])
    assert mirrored.id == result.id and not playlist_service.playlist_tracks(db, result.id)


def test_mirror_invalid_files_and_transaction_failure_preserve_existing(database, monkeypatch):
    db, user, other, tracks, sessions = database
    pl = playlist_service.mirror(db, user.id, 'Synthetic', [tracks[0].id])
    with pytest.raises(HTTPException): playlist_service.mirror(db, user.id, 'Synthetic', [999999])
    assert [row.file_id for row, _ in playlist_service.playlist_tracks(db, pl.id)] == [tracks[0].id]
    original_commit = db.commit
    def fail(): raise RuntimeError('synthetic commit failure')
    monkeypatch.setattr(db, 'commit', fail)
    with pytest.raises(RuntimeError): playlist_service.mirror(db, user.id, 'Synthetic', [tracks[1].id])
    monkeypatch.setattr(db, 'commit', original_commit)
    assert [row.file_id for row, _ in playlist_service.playlist_tracks(db, pl.id)] == [tracks[0].id]
    monkeypatch.setattr(db, 'commit', fail)
    with pytest.raises(RuntimeError): playlist_service.mirror(db, user.id, 'New', [tracks[1].id])
    assert db.scalar(select(playlist.Playlist).where(playlist.Playlist.name == 'New')) is None


def test_mirror_scopes_names_to_owner_and_validates_http_body(database):
    db, user, other, tracks, sessions = database
    original = playlist_service.mirror(db, other.id, 'Synthetic', [tracks[0].id])
    mine = playlist_service.mirror(db, user.id, 'Synthetic', [tracks[1].id])
    assert mine.id != original.id
    app = FastAPI(); app.include_router(playlist_router.router)
    def session():
        with sessions() as request_db: yield request_db
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    for ids in [[tracks[0].id, tracks[0].id], [-1], [True], ['1'], [1]*5001]:
        assert client.post('/api/playlists/mirror', json={'name': 'Synthetic', 'file_ids': ids}).status_code == 422
    resp = client.post('/api/playlists/mirror', json={'name': 'Synthetic', 'file_ids': [tracks[2].id]})
    assert resp.status_code == 200 and resp.json()['tracks'][0]['file_id'] == tracks[2].id
    assert [row.file_id for row, _ in playlist_service.playlist_tracks(db, original.id)] == [tracks[0].id]


def stage_sql(tmp_path, actor_id):
    path = tmp_path / 'synthetic.sql'; raw = _synthetic_dump().encode(); path.write_bytes(raw)
    snapshot = imports._scan_structure(path)
    source_id, source_path = imports._resolve_source_folder(snapshot, 'Music', None)
    import_id = 'f'*32
    state = imports.ImportSession(import_id=import_id, path=path, display_name='synthetic.sql', size_bytes=len(raw),
        digest=hashlib.sha256(raw).hexdigest(), actor_id=actor_id, source_folder_id=source_id,
        source_folder_path=source_path, selected_folder_ids=imports._select_subtree(snapshot, source_id),
        target_root_id=None, include_non_audio=False, snapshot=snapshot,
        preview={'summary': {'can_run': True}, 'warnings': []}, created_at=time.time())
    imports._SESSIONS[import_id] = state
    return import_id, state


@pytest.mark.parametrize('kind', ['sql', 'tree'])
@pytest.mark.parametrize('audit_fails', [False, True])
def test_import_and_router_audit_commit_or_rollback_together(database, tmp_path, monkeypatch, kind, audit_fails):
    db, user, other, tracks, sessions = database
    # Remove fixture tracks: exact new-file assertions use the import's own entries.
    for track in tracks: db.delete(track)
    db.commit()
    monkeypatch.setattr(imports, '_staging_root', lambda: tmp_path)
    if kind == 'sql': import_id, state = stage_sql(tmp_path, user.id)
    else:
        staged = imports.stage_vfs_upload(UploadFile(filename='synthetic.sql', file=BytesIO(_synthetic_dump().encode())), user.id)
        import_id = staged['import_id']; state = imports._SESSIONS[import_id]
    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []})
    original_audit = admin_vfs_router._audit
    def failing_audit(*args, **kwargs): original_audit(*args, **kwargs); raise RuntimeError('synthetic audit failure')
    if audit_fails: monkeypatch.setattr(admin_vfs_router, '_audit', failing_audit)
    commit_count = Mock(wraps=db.commit); monkeypatch.setattr(db, 'commit', commit_count)
    def run():
        if kind == 'sql':
            return admin_vfs_router.run_vfs_sql_import(import_id, admin_vfs_router.VfsImportRunRequest(confirm=True), request, db, user)
        return admin_vfs_router.run_vfs_tree_import_route(import_id,
            admin_vfs_router.VfsTreeRunRequest(selected_folder_ids=[1], confirm=True), request, db, user)
    try:
        if audit_fails:
            with pytest.raises(HTTPException): run()
            assert db.query(vfs.VFile).count() == 0
            assert db.query(auth.AdminAuditEvent).count() == 0
            assert state.status == 'failed' and state.path.is_file()
            commit_count.assert_not_called()
        else:
            result = run()
            assert result['files_imported'] > 0 and state.status == 'completed'
            assert db.query(auth.AdminAuditEvent).count() == 1
            commit_count.assert_called_once()
            if kind == 'sql': assert state.path is None
    finally:
        imports._SESSIONS.pop(import_id, None)
        imports._remove_file(state.path); imports._remove_file(state.sqlite_path)

@pytest.mark.asyncio
async def test_cover_dns_reply_from_before_shutdown_cannot_start_producer(monkeypatch):
    service = cover_cache_service.CoverCacheService()
    monkeypatch.setattr(service, 'get_cached_item', lambda *a, **kw: None)
    entered = asyncio.Event(); release = asyncio.Event()
    async def dns(url): entered.set(); await release.wait(); return True
    monkeypatch.setattr(cover_cache_service, 'is_safe_host', dns)
    producer = AsyncMock(); monkeypatch.setattr(service, '_do_fetch', producer)
    waiter = asyncio.create_task(service.fetch_and_cache('https://synthetic.example/cover'))
    await entered.wait(); await service.close(); release.set()
    assert await waiter == (None, None); producer.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('entry', ['service', 'utility'])
async def test_transcode_entry_reaps_before_removing_source(monkeypatch, tmp_path, entry):
    from app.utils import ffmpeg_transcoder
    proc = FakeProcess(); module = transcoding_service if entry == 'service' else ffmpeg_transcoder
    monkeypatch.setattr(module, 'ensure_ffmpeg_binaries', lambda: True)
    src_paths = []
    async def spawn(*args, **kwargs):
        src_paths.append(Path(args[args.index('-i') + 1])); return proc
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    if entry == 'service':
        monkeypatch.setattr(settings, 'TRANSCODE_CACHE_DIR', tmp_path)
        service = transcoding_service.TranscodingService()
        async def download(job, path):
            await service._disk_call(path.write_bytes, b'synthetic audio')
            return 15
        monkeypatch.setattr(service, '_download_track_to_file', download)
        job = transcoding_service.TranscodeJob(1, 'Synthetic.flac', 123, False, 'audio/flac', 0, 'a'*32)
        operation = service._transcode_track(job)
    else:
        operation = ffmpeg_transcoder.transcode_from_bytes(b'synthetic audio', str(tmp_path/'output.mp3'))
    original_kill = proc.kill
    def kill():
        assert src_paths[0].is_file(); original_kill()
    proc.kill = kill
    task = asyncio.create_task(operation); await proc.entered.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert proc.killed and proc.drained and not src_paths[0].exists()


@pytest.mark.asyncio
async def test_transcode_track_offloads_io_and_duration_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'TRANSCODE_CACHE_DIR', tmp_path)
    service = transcoding_service.TranscodingService()
    monkeypatch.setattr(transcoding_service, 'ensure_ffmpeg_binaries', lambda: True)
    async def download(job, path):
        await service._disk_call(path.write_bytes, b'synthetic audio')
        return 15
    monkeypatch.setattr(service, '_download_track_to_file', download)
    
    # Mock subprocess communication returning valid fake MP3 bytes (>1024 bytes)
    fake_mp3 = b'ID3' + b'\x00' * 2048
    async def fake_communicate(proc, timeout=None):
        return fake_mp3, b''
    monkeypatch.setattr(transcoding_service, 'communicate_with_cleanup', fake_communicate)

    class FakeProc:
        returncode = 0
    async def fake_spawn(*args, **kwargs):
        Path(args[-1]).write_bytes(fake_mp3)
        assert kwargs['stdout'] == asyncio.subprocess.DEVNULL
        return FakeProc()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', fake_spawn)

    # Track calls to asyncio.to_thread
    thread_targets = []
    original_to_thread = asyncio.to_thread
    async def tracking_to_thread(func, *args, **kwargs):
        thread_targets.append(getattr(func, '__name__', str(func)))
        return await original_to_thread(func, *args, **kwargs)
    monkeypatch.setattr(asyncio, 'to_thread', tracking_to_thread)

    def fake_duration(path):
        return 123.45
    monkeypatch.setattr(transcoding_service, 'get_mp3_duration', fake_duration)
    
    job = transcoding_service.TranscodeJob(1, 'Synthetic.flac', 123, False, 'audio/flac', 0, 'c'*32)
    await service._transcode_track(job)

    assert job.status == transcoding_service.TranscodeStatus.READY
    assert job.duration == 123.45
    # Verify that duration probe, write_file, manifest and cache cleanup were offloaded via asyncio.to_thread
    assert 'fake_duration' in thread_targets
    assert 'write_bytes' in thread_targets
    assert 'replace' in thread_targets
    assert 'write_cache_manifest' in thread_targets
    assert '_cleanup_cache' in thread_targets

