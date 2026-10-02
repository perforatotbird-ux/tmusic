"""Resource regressions: disposable SQLite and synthetic sources, no live services."""
import asyncio
import importlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.models.base import Base
from app.models.vfs import VFile
from app.models.share import ShareLink
from app.services.ram_cache_service import RamCacheService

ss = importlib.import_module('app.services.stream_service')
tc = importlib.import_module('app.services.transcoding_service')
tg = importlib.import_module('app.tg.manager')


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/api/stream/1', '/api/share/synthetic/stream', '/api/stream/transcode-events'])
async def test_database_released_before_long_response(monkeypatch, tmp_path, path):
    dbmod = importlib.import_module('app.core.database')
    deps = importlib.import_module('app.api.dependencies')
    router = importlib.import_module('app.api.stream_router')
    share_router = importlib.import_module('app.api.share_router')
    engine = create_engine('sqlite:///' + str(tmp_path / 'test.db'), poolclass=QueuePool,
                          pool_size=1, max_overflow=0, pool_timeout=.1,
                          connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(VFile(id=1, name='synthetic.mp3', size=2048, mime_type='audio/mpeg', is_encrypted=False))
        db.add(ShareLink(token='synthetic', user_id=1, file_id=1)); db.commit()
    monkeypatch.setattr(dbmod, 'SessionLocal', factory)
    def authenticate(request, credentials, db, **kwargs):
        db.execute(text('SELECT 1'))
        return SimpleNamespace(id=1)
    monkeypatch.setattr(deps, '_authenticate_request', authenticate)
    entered, gate = asyncio.Event(), asyncio.Event()
    async def slow_body(*args, **kwargs):
        yield b'x' * 1024
        entered.set(); await gate.wait()
        yield b'x' * 1024
    monkeypatch.setattr(ss.stream_service, 'get_stream_generator', slow_body)
    queue = asyncio.Queue(); queue.put_nowait({'event': 'ping'})
    real_get = queue.get
    async def observed_get():
        if queue.empty(): entered.set()
        return await real_get()
    queue.get = observed_get
    monkeypatch.setattr(router.transcoding_service, 'subscribe', lambda uid: queue)
    unsubscribe = []
    monkeypatch.setattr(router.transcoding_service, 'unsubscribe', lambda uid, q: unsubscribe.append(uid))
    app = FastAPI(); app.include_router(router.router); app.include_router(share_router.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            task = asyncio.create_task(client.get(path))
            try:
                await asyncio.wait_for(entered.wait(), 3)
                assert engine.pool.checkedout() == 0
                with engine.connect() as connection:
                    assert connection.scalar(text('SELECT 1')) == 1
            finally:
                gate.set(); queue.put_nowait(None)
                response = await task
            assert response.status_code == 200
            assert engine.pool.checkedout() == 0
            if 'events' in path: assert unsubscribe == [1]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_subsonic_releases_read_session(monkeypatch, tmp_path):
    from app.api import subsonic_router
    from starlette.requests import Request
    engine = create_engine('sqlite:///' + str(tmp_path / 'subsonic.db'), poolclass=QueuePool,
                          pool_size=1, max_overflow=0)
    factory = sessionmaker(bind=engine)
    db = factory(); db.execute(text('SELECT 1'))
    vf = VFile(id=1, name='synthetic.mp3', size=4, mime_type='audio/mpeg')
    monkeypatch.setattr(ss.stream_service, 'get_track_or_404', lambda *args: vf)
    monkeypatch.setattr(subsonic_router, 'run_password_work', AsyncMock(return_value=SimpleNamespace(id=1)))
    async def source(*args, **kwargs): yield b'abcd'
    monkeypatch.setattr(ss.stream_service, 'get_stream_generator', source)
    request = Request({'type': 'http', 'method': 'GET', 'path': '/rest/stream', 'headers': []})
    try:
        response = await subsonic_router._handle('stream', request, db, {'id': 'fl-1'}, 'json')
        assert engine.pool.checkedout() == 0
        assert b''.join([chunk async for chunk in response.body_iterator]) == b'abcd'
    finally:
        db.close(); engine.dispose()


@pytest.mark.asyncio
async def test_reservations_bound_concurrent_buffers_and_release_on_cancel():
    cache = RamCacheService(max_bytes=100)
    async with cache.reserve_buffer(30) as first:
        assert first and cache._buffer_bytes == 60
        async with cache.reserve_buffer(30) as second:
            assert not second and cache._buffer_bytes == 60
        async with cache.reserve_buffer(20) as third:
            assert third and cache._buffer_bytes == 100
    assert cache._buffer_bytes == 0
    entered = asyncio.Event()
    async def owner():
        async with cache.reserve_buffer(40) as accepted:
            assert accepted; entered.set(); await asyncio.Event().wait()
    task = asyncio.create_task(owner()); await entered.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert cache._buffer_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('budget,complete', [(16, True), (1024, True), (16, False)])
async def test_full_stream_never_buffers_oversized_track(monkeypatch, budget, complete):
    total = 128
    cache = RamCacheService(max_bytes=budget)
    monkeypatch.setattr(ss, 'ram_cache', cache)
    service = ss.StreamService()
    monkeypatch.setattr(service, 'get_plain_track_size', AsyncMock(return_value=total))
    vf = VFile(id=1, name='synthetic.mp3', size=total, mime_type='audio/mpeg', is_encrypted=True)
    async def chunks(**kwargs):
        for _ in range(8 if complete else 7): yield b'x' * 16
    monkeypatch.setattr(ss.fast_streamer, 'stream_file', chunks)
    monkeypatch.setattr(ss.DecryptionService, 'decrypt_stream', chunks)
    gen = service.get_stream_generator(vf, 0, total, prefer_transcoded=False)
    received = 0
    async def consume():
        nonlocal received
        async for chunk in gen:
            received += len(chunk)
            buf = gen.ag_frame.f_locals['buf']
            assert (buf is None) == (budget < total * 2)
    if complete:
        await consume()
        assert received == total
        assert cache.stats()['bytes'] == (total if budget >= total * 2 else 0)
    else:
        with pytest.raises(RuntimeError, match='incomplete'): await consume()
        assert cache.stats()['bytes'] == 0
    assert cache._buffer_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False, True])
async def test_unregistered_telegram_client_stopped(monkeypatch, cancel):
    entered = asyncio.Event(); clients = []
    class Client:
        def __init__(self, **kwargs): self.stopped = 0; clients.append(self)
        async def start(self): pass
        async def get_me(self):
            entered.set()
            if cancel: await asyncio.Event().wait()
            raise RuntimeError('synthetic failure')
        async def stop(self): self.stopped += 1
    monkeypatch.setattr(tg, 'Client', Client)
    manager = tg.TelegramManager()
    task = asyncio.create_task(manager._start_single_client(1, 'synthetic', 'synthetic', 1, 'synthetic'))
    if cancel:
        await entered.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    else:
        assert await task is None
    assert clients[0].stopped == 1
    assert not manager.clients


def job(i, status, age=0):
    return tc.TranscodeJob(i, 'synthetic.flac', 1, False, 'audio/flac', 0, f'synthetic-{i}',
                          status=status, finished_at=time.time() - age)


def test_terminal_jobs_bounded_without_evicting_active(monkeypatch):
    service = tc.TranscodingService()
    monkeypatch.setattr(tc, 'MAX_COMPLETED_JOBS', 3)
    service._jobs = {i: job(i, tc.TranscodeStatus.FAILED, i) for i in range(10)}
    service._jobs[20] = job(20, tc.TranscodeStatus.PROCESSING, tc.COMPLETED_JOB_TTL * 2)
    service._jobs[21] = job(21, tc.TranscodeStatus.PENDING, tc.COMPLETED_JOB_TTL * 2)
    service._prune_jobs()
    assert len(service._jobs) == 5 and 20 in service._jobs and 21 in service._jobs
    service._jobs[1] = job(1, tc.TranscodeStatus.READY, tc.COMPLETED_JOB_TTL + 1)
    monkeypatch.setattr(service, '_check_disk_cache', lambda i: {'cached_path': 'synthetic'} if i == 1 else None)
    assert service.get_status(1) == tc.TranscodeStatus.READY and 1 not in service._jobs


@pytest.mark.asyncio
async def test_idle_worker_sweeps_and_shutdown_clears_jobs(monkeypatch):
    service = tc.TranscodingService()
    monkeypatch.setattr(tc, 'JOB_SWEEP_INTERVAL', .01)
    service._jobs[1] = job(1, tc.TranscodeStatus.FAILED, tc.COMPLETED_JOB_TTL + 1)
    service.start(1)
    try:
        for _ in range(30):
            if not service._jobs: break
            await asyncio.sleep(.01)
        assert not service._jobs
        service._jobs[2] = job(2, tc.TranscodeStatus.PENDING)
    finally:
        await service.shutdown()
    assert not service._jobs and not service._workers and service._queue.empty()


def test_not_needed_job_has_terminal_status(monkeypatch):
    service = tc.TranscodingService(); service._started = True
    service._queue = tc.TranscodePriorityQueue(100)
    monkeypatch.setattr(service, '_check_disk_cache', lambda i: None)
    monkeypatch.setattr(tc, 'needs_transcoding', lambda *args: False)
    assert service.request_transcode(VFile(id=1, name='synthetic.mp3', size=1)) == tc.TranscodeStatus.NOT_NEEDED
    assert service._jobs[1].finished_at is not None


@pytest.mark.asyncio
async def test_sse_disconnect_before_first_iteration_unsubscribes_once(monkeypatch):
    from app.api import stream_router
    from starlette.requests import Request
    from starlette.responses import StreamingResponse
    queue = asyncio.Queue(); removed = []
    monkeypatch.setattr(stream_router.transcoding_service, 'subscribe', lambda uid: queue)
    monkeypatch.setattr(stream_router.transcoding_service, 'unsubscribe', lambda uid, q: removed.append(uid))
    async def disconnect(*args): raise asyncio.CancelledError()
    monkeypatch.setattr(StreamingResponse, '__call__', disconnect)
    response = await stream_router.transcode_events(
        Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': []}),
        SimpleNamespace(id=1), SimpleNamespace(close=lambda: None))
    with pytest.raises(asyncio.CancelledError): await response({}, None, None)
    assert removed == [1]
    response.release(); assert removed == [1]
