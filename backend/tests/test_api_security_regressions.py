"""Security regression tests: isolated DB, no Telegram or provider requests."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api import admin_router, metadata_router, stream_router, subsonic_admin_router, subsonic_router
from app.api.dependencies import get_current_user, get_admin_user
from app.core.database import get_db
from app.core import body_limits
from app.core.user_cache import get_user_by_username, invalidate_user
from app.models.auth import AppUser, BackupRun
from app.services import admin_service, subsonic_auth
from app.services.transcoding_service import (
    TranscodingService, TranscodeStatus, MAX_USER_ACTIVE_TRANSCODES,
)


@pytest.fixture
def user_db(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'SESSION_SECRET', 'isolated-review-test-secret')
    engine = create_engine('sqlite:///:memory:')
    AppUser.__table__.create(engine)
    BackupRun.__table__.create(engine)
    with Session(engine) as db:
        user = AppUser(username='api_review_user', password_hash='unused', role='user', is_active=True)
        db.add(user)
        db.commit()
        invalidate_user()
        yield db, user
        invalidate_user()
    engine.dispose()


def test_rotation_revokes_old_token_for_detached_user(user_db):
    db, user = user_db
    old = subsonic_auth.ensure_subsonic_token(db, user)
    detached = get_user_by_username(db, user.username)
    assert detached not in db
    result = asyncio.run(subsonic_admin_router.rotate_credentials(db=db, user=detached))
    stored = db.scalar(select(AppUser).where(AppUser.username == user.username))
    db.refresh(stored)
    assert subsonic_auth.decrypt_token(stored.subsonic_token_enc) == result['token']
    assert subsonic_auth.authenticate_subsonic(db, user.username, old, None, None) is None
    assert subsonic_auth.authenticate_subsonic(db, user.username, result['token'], None, None) is not None
    assert get_user_by_username(db, user.username).subsonic_token_enc == stored.subsonic_token_enc


def test_initial_token_is_persisted_for_detached_user(user_db):
    db, user = user_db
    detached = get_user_by_username(db, user.username)
    token = subsonic_auth.ensure_subsonic_token(db, detached)
    assert subsonic_auth.authenticate_subsonic(db, user.username, token, None, None) is not None


def test_missing_encryption_secret_does_not_revoke_existing_token(user_db, monkeypatch):
    from app.core.config import settings
    db, user = user_db
    old = subsonic_auth.ensure_subsonic_token(db, user)
    encrypted = user.subsonic_token_enc
    monkeypatch.setattr(settings, 'SESSION_SECRET', None)
    with pytest.raises(RuntimeError):
        subsonic_auth.ensure_subsonic_token(db, user, rotate=True)
    db.refresh(user)
    assert user.subsonic_token_enc == encrypted


@pytest.mark.parametrize('path', list(body_limits.UPLOAD_BODY_LIMITS) + [
    '/api/auth/login', '/api/admin/vfs/import/abc/tree-run',
    '/%61pi/auth/avatar', '/api/auth/avatar/',
])
@pytest.mark.parametrize('declared', [False, True])
def test_body_limit_rejects_before_downstream_parsing(path, declared, monkeypatch):
    monkeypatch.setattr(body_limits, 'MAX_JSON_BODY_BYTES', 8)
    monkeypatch.setattr(body_limits, 'UPLOAD_BODY_LIMITS', {p: 8 for p in body_limits.UPLOAD_BODY_LIMITS})
    called = []
    messages = []
    chunks = iter([{'type': 'http.request', 'body': b'1234', 'more_body': True},
                   {'type': 'http.request', 'body': b'56789', 'more_body': False}])
    async def downstream(scope, receive, send):
        called.append(True)
    async def receive():
        return next(chunks)
    async def send(message):
        messages.append(message)
    scope = {'type': 'http', 'method': 'POST', 'path': path,
             'headers': [(b'content-length', b'9')] if declared else []}
    asyncio.run(body_limits.BodySizeLimitMiddleware(downstream)(scope, receive, send))
    assert called == []
    assert messages[0]['status'] == 413


def test_body_replay_preserves_bytes_and_disconnect(monkeypatch):
    monkeypatch.setattr(body_limits, 'MAX_JSON_BODY_BYTES', 100000)
    received = []
    chunks = iter([{'type': 'http.request', 'body': b'a'*70000, 'more_body': True},
                   {'type': 'http.request', 'body': b'b', 'more_body': False},
                   {'type': 'http.disconnect'}])
    async def receive():
        return next(chunks)
    async def downstream(scope, receive, send):
        while True:
            message = await receive()
            received.append(message)
            if message['type'] == 'http.disconnect':
                break
    asyncio.run(body_limits.BodySizeLimitMiddleware(downstream)(
        {'type': 'http', 'method': 'POST', 'path': '/api/auth/login', 'headers': []}, receive, None))
    assert b''.join(m.get('body', b'') for m in received) == b'a'*70000+b'b'
    assert received[-2]['more_body'] is False
    assert received[-1]['type'] == 'http.disconnect'


@pytest.mark.parametrize('method', ['search2', 'search3'])
@pytest.mark.parametrize('count', ['-1', '501', '1000000', 'bad'])
def test_subsonic_search_counts_are_bounded(method, count, monkeypatch):
    monkeypatch.setattr(subsonic_router, '_http_auth_user', lambda *args: SimpleNamespace(id=1))
    db = Mock()
    response = asyncio.run(subsonic_router._handle(method, None, db,
        {'query': 'song', 'songCount': count}, 'json'))
    assert b'Search' in response.body or b'Invalid search count' in response.body
    db.scalars.assert_not_called()


def client_for(router, db=None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=888, role='user', username='regular')
    app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(id=1, role='admin', username='admin')
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


@pytest.mark.parametrize('ids', [[], list(range(1, 52)), [0], [-1]])
def test_prefetch_invalid_batches_rejected(ids):
    db = Mock()
    response = client_for(stream_router.router, db).post('/api/stream/transcode/prefetch', json=ids)
    assert response.status_code == 422
    db.scalars.assert_not_called()


def test_prefetch_uses_single_query_and_deduplicates(monkeypatch):
    from app.core.rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(stream_router, 'limiter', SlidingWindowRateLimiter())
    track = SimpleNamespace(id=1, name='track.flac', mime_type='audio/flac')
    db = Mock()
    db.scalars.return_value.all.return_value = [track]
    monkeypatch.setattr(stream_router.transcoding_service, 'get_status', lambda _: TranscodeStatus.PENDING)
    enqueue = Mock(return_value=TranscodeStatus.PENDING)
    monkeypatch.setattr(stream_router.transcoding_service, 'request_transcode', enqueue)
    response = client_for(stream_router.router, db).post('/api/stream/transcode/prefetch', json=[1,1])
    assert response.status_code == 200
    db.scalars.assert_called_once()
    enqueue.assert_called_once()


def test_prefetch_has_per_user_rate_limit(monkeypatch):
    from app.core.rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(stream_router, 'limiter', SlidingWindowRateLimiter())
    db = Mock()
    db.scalars.return_value.all.return_value = []
    client = client_for(stream_router.router, db)
    for _ in range(10):
        assert client.post('/api/stream/transcode/prefetch', json=[1]).status_code == 200
    assert client.post('/api/stream/transcode/prefetch', json=[1]).status_code == 429


def track(i):
    return SimpleNamespace(id=i, name='track.flac', mime_type='audio/flac', size=1,
                           updated_at=None, is_encrypted=False)


def test_transcode_queue_full_leaves_no_orphan_job(monkeypatch):
    service = TranscodingService()
    service._started = True
    service._queue = asyncio.PriorityQueue(maxsize=1)
    monkeypatch.setattr(service, 'get_status', lambda _: TranscodeStatus.PENDING)
    service.request_transcode(track(1), requester_user_id=10)
    with pytest.raises(HTTPException) as error:
        service.request_transcode(track(2), requester_user_id=10)
    assert error.value.status_code == 429
    assert 2 not in service._jobs


def test_transcode_user_active_quota(monkeypatch):
    from app.services import transcoding_service as module
    from app.core.rate_limiter import SlidingWindowRateLimiter
    monkeypatch.setattr(module, 'limiter', SlidingWindowRateLimiter())
    service = TranscodingService()
    service._started = True
    service._queue = asyncio.PriorityQueue(maxsize=100)
    monkeypatch.setattr(service, 'get_status', lambda _: TranscodeStatus.PENDING)
    for i in range(MAX_USER_ACTIVE_TRANSCODES):
        service.request_transcode(track(i), requester_user_id=99)
    with pytest.raises(HTTPException) as error:
        service.request_transcode(track(1000), requester_user_id=99)
    assert error.value.status_code == 429
    assert service.request_transcode(track(0), requester_user_id=99) == TranscodeStatus.PENDING


def test_catalog_enrichment_denies_regular_user_before_provider_call(monkeypatch):
    lookup = Mock()
    monkeypatch.setattr(metadata_router.lastfm_service, 'get_artist_info', lookup)
    response = client_for(metadata_router.router).post('/api/metadata/artists/1/enrich')
    assert response.status_code == 403
    lookup.assert_not_called()


def test_transcode_status_hides_internal_path(monkeypatch):
    monkeypatch.setattr(stream_router.stream_service, 'get_track_or_404', lambda *args: track(1))
    monkeypatch.setattr(stream_router.transcoding_service, 'get_status', lambda _: TranscodeStatus.READY)
    monkeypatch.setattr(stream_router.transcoding_service, 'get_result', lambda _: {
        'cached_path': '/private/server/cache/file.mp3', 'mp3_size': 42, 'duration': 2})
    response = client_for(stream_router.router).get('/api/stream/1/transcode-status')
    assert response.status_code == 200
    assert 'cached_path' not in response.json()
    assert '/private' not in response.text


def test_backup_errors_do_not_leak_to_client(monkeypatch):
    secret = 'postgres://secret@internal/private.sql'
    monkeypatch.setattr(admin_router.admin_service, 'run_pg_dump', lambda *args, **kw: {'ok':False, 'error':secret})
    client = client_for(admin_router.router)
    response = client.post('/api/admin/backups/run')
    assert response.status_code == 500
    assert secret not in response.text
    def fail(*args, **kw):
        raise RuntimeError(secret)
    monkeypatch.setattr(admin_router.admin_service, 'restore_backup', fail)
    response = client.post('/api/admin/backups/test.dump/restore', json={'dry_run':False,'confirm':'test.dump'})
    assert response.status_code == 500
    assert secret not in response.text


def test_backup_run_errors_are_redacted_even_for_old_records(user_db):
    db, _ = user_db
    run = BackupRun(kind='dump',status='failed',error='private database credentials')
    db.add(run); db.commit()
    assert admin_service.list_backup_runs(db)[0]['error'] == 'Backup operation failed. See server logs.'
    admin_service._finish_backup_run(db, run, 'failed', error='private internal error')
    assert 'private' not in run.error


def test_multipart_limit_runs_before_auth_and_form_parsing(monkeypatch):
    from fastapi import Depends, File, UploadFile
    monkeypatch.setattr(body_limits, 'UPLOAD_BODY_LIMITS', {'/api/auth/avatar': 300})
    app = FastAPI()
    app.add_middleware(body_limits.BodySizeLimitMiddleware)
    calls = []
    def auth():
        calls.append('auth')
        raise HTTPException(status_code=401)
    @app.post('/api/auth/avatar')
    async def upload(file: UploadFile = File(...), user=Depends(auth)):
        calls.append('handler')
    client = TestClient(app)
    response = client.post('/api/auth/avatar', files={'file': ('huge.png', b'x'*301)})
    assert response.status_code == 413
    assert calls == []
    response = client.post('/api/auth/avatar', files={'file': ('small.png', b'x')})
    assert response.status_code == 401
    assert calls == ['auth']


@pytest.mark.parametrize('count', ['0', '500'])
def test_subsonic_search_boundary_counts_accepted(count, monkeypatch):
    monkeypatch.setattr(subsonic_router, '_http_auth_user', lambda *args: SimpleNamespace(id=1))
    db = Mock()
    db.scalars.return_value.all.return_value = []
    response = asyncio.run(subsonic_router._handle('search3', None, db,
        {'query': 'song', 'songCount': count, 'albumCount': count, 'artistCount': count}, 'json'))
    assert b'"status":"ok"' in response.body
    assert db.scalars.call_count == 3


def test_catalog_enrichment_allows_editor(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'LASTFM_ENABLED', True)
    monkeypatch.setattr(metadata_router.lastfm_service, 'metadata_ready', lambda: True)
    monkeypatch.setattr(metadata_router.lastfm_service, 'get_artist_info', lambda _: {'summary':'Safe bio'})
    artist = SimpleNamespace(id=1, display_name='Artist', bio=None, wikipedia_url=None, image_url=None, mbid=None)
    db = Mock()
    db.scalar.return_value = artist
    app = FastAPI()
    app.include_router(metadata_router.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username='editor', role='editor')
    app.dependency_overrides[get_db] = lambda: db
    response = TestClient(app).post('/api/metadata/artists/1/enrich')
    assert response.status_code == 200
    assert response.json()['updated'] is True
    db.commit.assert_called_once()
