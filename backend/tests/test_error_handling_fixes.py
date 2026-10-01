"""Error handling regressions: synthetic credentials, local SQLite and fake providers only."""
import asyncio
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import InvalidToken
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from app.tg import manager
from app.services import lastfm_service as lastfm, vfs_import_service as imports
from app.services.playlist_service import playlist_service
from app.services.stream_service import StreamService
from app.api import admin_vfs_router, playlist_router, playback_router
from app.core.config import settings
from app.models.base import Base
from app.models import auth, music, playback, playlist, share, vfs
from app.schemas.playlist import PlaylistCreate


@pytest.mark.parametrize('value', ['legacy-session-example', '0123456789abcdef0123456789abcdef', None])
def test_legacy_telegram_plaintext_remains_compatible(value):
    assert manager.decrypt_session_string(value, 'synthetic-key') == value
    sealed, changed = manager.ensure_encrypted(value, 'synthetic-key')
    assert changed is bool(value)
    assert manager.decrypt_session_string(sealed, 'synthetic-key') == value


@pytest.mark.parametrize('kind', ['wrong_key', 'missing_key', 'truncated', 'tampered'])
def test_telegram_encrypted_failures_never_become_plaintext(kind):
    token = manager.encrypt_session_string('synthetic-data', 'old-synthetic-key')
    secret = 'wrong-synthetic-key' if kind == 'wrong_key' else None if kind == 'missing_key' else 'old-synthetic-key'
    if kind == 'truncated': token = token[:12]
    if kind == 'tampered': token = token[:-9] + 'AAAAAAAA='
    account = SimpleNamespace(id=1, session_string='legacy-plaintext', api_hash=token)
    for operation in [lambda: manager.decrypt_session_string(token, secret),
                      lambda: manager.ensure_encrypted(token, secret),
                      lambda: manager.seal_tg_account(account, secret)]:
        with pytest.raises(manager.TGCredentialError): operation()
    assert account.session_string == 'legacy-plaintext' and account.api_hash == token


def test_matching_telegram_key_does_not_rewrap_ciphertext():
    token = manager.encrypt_session_string('synthetic', 'synthetic-key')
    assert manager.ensure_encrypted(token, 'synthetic-key') == (token, False)
    assert manager.is_encrypted(token, 'synthetic-key')


def test_telegram_unexpected_programming_error_propagates(monkeypatch):
    token = manager.encrypt_session_string('synthetic', 'synthetic-key')
    monkeypatch.setattr(manager, '_fernet_for_secret', Mock(side_effect=RuntimeError('synthetic failure')))
    with pytest.raises(RuntimeError): manager.ensure_encrypted(token, 'synthetic-key')


@pytest.mark.parametrize('kind', ['sql', 'connect', 'validation'])
def test_tree_import_sanitises_unexpected_errors_and_releases_lock(tmp_path, monkeypatch, kind):
    path = tmp_path / 'stage.sqlite'; sqlite3.connect(path).close()
    ident = 'a' * 32
    state = imports.ImportSession(import_id=ident, path=None, sqlite_path=path, actor_id=1, created_at=time.time())
    monkeypatch.setitem(imports._SESSIONS, ident, state)
    db = Mock()
    if kind == 'connect': monkeypatch.setattr(imports.sqlite3, 'connect', Mock(side_effect=OSError('/private/synthetic-path')))
    if kind == 'validation': monkeypatch.setattr(imports, '_expand_import_folders', Mock(side_effect=imports.VfsImportError('Выберите папку', 400)))
    request = Request({'type':'http','method':'POST','path':'/','headers':[]})
    with pytest.raises(HTTPException) as caught:
        admin_vfs_router.run_vfs_tree_import_route(ident,
            admin_vfs_router.VfsTreeRunRequest(selected_folder_ids=[1], confirm=True), request, db, SimpleNamespace(id=1))
    assert caught.value.status_code == (400 if kind == 'validation' else 500)
    status = imports.get_import_status(ident, 1)
    for text in (caught.value.detail, status['error'], status['progress']['message']):
        assert 'no such table' not in text and '/private/' not in text
    if kind != 'validation': assert 'Код ошибки:' in caught.value.detail
    db.rollback.assert_called_once()
    assert imports._RUN_LOCK.acquire(blocking=False)
    imports._RUN_LOCK.release()


@pytest.fixture
def database():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = auth.AppUser(username='synthetic', password_hash='placeholder', role='user')
        track = vfs.VFile(name='Synthetic.mp3', size=4, mime_type='audio/mpeg', is_trash=False)
        trashed = vfs.VFile(name='Trashed.mp3', size=4, mime_type='audio/mpeg', is_trash=True)
        db.add_all([user, track, trashed]); db.commit()
        yield db, user, track, trashed
    engine.dispose()


@pytest.mark.parametrize('kind', ['missing', 'trashed', 'commit'])
def test_atomic_create_with_track_failure_leaves_no_playlist(database, monkeypatch, kind):
    db, user, track, trashed = database
    if kind == 'commit': monkeypatch.setattr(db, 'commit', Mock(side_effect=RuntimeError('synthetic commit failure')))
    file_id = 99999 if kind == 'missing' else trashed.id if kind == 'trashed' else track.id
    with pytest.raises(HTTPException if kind != 'commit' else RuntimeError):
        playlist_service.create_playlist(db, user.id, 'Synthetic', file_id)
    assert db.scalar(select(func.count(playlist.Playlist.id))) == 0
    assert db.scalar(select(func.count(playlist.PlaylistItem.id))) == 0


@pytest.mark.parametrize('initial', [True, False])
def test_atomic_create_summary_and_single_commit(database, monkeypatch, initial):
    db, user, track, _ = database
    commit = Mock(wraps=db.commit); monkeypatch.setattr(db, 'commit', commit)
    body = PlaylistCreate(name=' Synthetic ', initial_file_id=track.id if initial else None)
    summary = playlist_router.create_playlist(body, db, user)
    assert summary.name == 'Synthetic' and summary.track_count == int(initial)
    assert db.scalar(select(func.count(playlist.PlaylistItem.id))) == int(initial)
    commit.assert_called_once()


@pytest.mark.parametrize('invalid', [0, -1, '1', True])
def test_initial_track_id_is_positive_strict_integer(invalid):
    from pydantic import ValidationError
    with pytest.raises(ValidationError): PlaylistCreate(name='Synthetic', initial_file_id=invalid)


def track(): return SimpleNamespace(id=71, name='Synthetic.mp3', size=4, mime_type='audio/mpeg', is_encrypted=False)
def request(): return SimpleNamespace(headers={})


@pytest.mark.parametrize('kind', ['raise', 'empty', 'oversize'])
def test_stream_preflight_failure_is_503_before_headers(monkeypatch, kind):
    service = StreamService(); closed=[]
    async def source(**kwargs):
        try:
            if kind == 'raise': raise RuntimeError('/private/synthetic')
            if kind == 'oversize': yield b'12345'
            if kind == 'empty': return
        finally: closed.append(True)
    monkeypatch.setattr('app.services.stream_service.fast_streamer.stream_file', source)
    monkeypatch.setattr('app.services.stream_service.ram_cache.get', AsyncMock(return_value=None))
    app=FastAPI()
    @app.get('/audio')
    async def endpoint(req: Request): return await service.build_stream_response(req, track())
    with TestClient(app, raise_server_exceptions=False) as client: response=client.get('/audio')
    assert response.status_code == 503 and response.json()['detail']=='Streaming service temporarily unavailable.'
    assert '/private/' not in response.text and closed


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['raise', 'eof'])
async def test_late_stream_failure_aborts_body_without_http_rewrite(monkeypatch, kind):
    service=StreamService(); closed=[]
    async def source(**kwargs):
        try:
            yield b'12'
            if kind == 'raise': raise RuntimeError('synthetic late failure')
        finally: closed.append(True)
    monkeypatch.setattr('app.services.stream_service.fast_streamer.stream_file', source)
    monkeypatch.setattr('app.services.stream_service.ram_cache.get', AsyncMock(return_value=None))
    response=await service.build_stream_response(request(), track())
    assert response.status_code==200
    assert await anext(response.body_iterator)==b'12'
    with pytest.raises(RuntimeError): await anext(response.body_iterator)
    assert closed


@pytest.mark.asyncio
async def test_cancel_during_preflight_closes_source(monkeypatch):
    service=StreamService(); entered=asyncio.Event(); closed=[]
    async def source(**kwargs):
        try:
            entered.set(); await asyncio.Event().wait(); yield b'1234'
        finally: closed.append(True)
    monkeypatch.setattr('app.services.stream_service.fast_streamer.stream_file', source)
    monkeypatch.setattr('app.services.stream_service.ram_cache.get', AsyncMock(return_value=None))
    pending=asyncio.create_task(service.build_stream_response(request(), track()))
    await entered.wait(); pending.cancel()
    with pytest.raises(asyncio.CancelledError): await pending
    assert closed


@pytest.mark.asyncio
async def test_disconnect_before_body_closes_prefetched_source(monkeypatch):
    service=StreamService(); closed=[]
    async def source(**kwargs):
        try: yield b'12'; yield b'34'
        finally: closed.append(True)
    monkeypatch.setattr('app.services.stream_service.fast_streamer.stream_file', source)
    monkeypatch.setattr('app.services.stream_service.ram_cache.get', AsyncMock(return_value=None))
    response=await service.build_stream_response(request(),track())
    async def failed_send(message): raise OSError('synthetic disconnect')
    with pytest.raises(ClientDisconnect): await response({'type':'http','asgi':{'spec_version':'2.4'}}, AsyncMock(), failed_send)
    assert closed


@pytest.mark.parametrize('result,expected', [
    (None,False), ({'error':9,'message':'private synthetic'},False), ({},False),
    ({'scrobbles':{'@attr':{'accepted':'0','ignored':'1'},'scrobble':{'ignoredMessage':{'code':'3'}}}},False),
    ({'scrobbles':{'@attr':{'accepted':'NaN'},'scrobble':{'ignoredMessage':{'code':'0'}}}},False),
    ({'scrobbles':{'@attr':{'accepted':'1','ignored':'0'},'scrobble':{'ignoredMessage':{'code':'0'}}}},True),
])
def test_lastfm_scrobble_outcomes_never_log_false_success(monkeypatch, caplog, result, expected):
    monkeypatch.setattr(lastfm,'scrobble_ready', lambda:True)
    monkeypatch.setattr(lastfm,'_post', lambda params:result)
    with caplog.at_level('INFO',logger='tmusic.scrobble'): outcome=lastfm.scrobble('Synthetic artist','Synthetic track')
    assert outcome['ok'] is expected
    assert ('Scrobbled:' in caplog.text) is expected
    assert 'private synthetic' not in caplog.text


@pytest.mark.parametrize('result,expected', [(None,False),({'error':9},False),
    ({'nowplaying':{'ignoredMessage':{'code':'1'}}},False),
    ({'nowplaying':{'ignoredMessage':{'code':'0'}}},True)])
def test_nowplaying_explicit_outcome(monkeypatch,result,expected):
    monkeypatch.setattr(lastfm,'scrobble_ready',lambda:True)
    monkeypatch.setattr(lastfm,'_post',lambda params:result)
    assert lastfm.update_now_playing('Synthetic','Synthetic')['ok'] is expected


@pytest.mark.asyncio
async def test_playback_history_survives_failed_external_delivery(database, monkeypatch):
    from app.schemas.playback import HistoryCreate
    db,user,vf,_=database
    monkeypatch.setattr(lastfm,'scrobble',Mock(side_effect=RuntimeError('synthetic provider failure')))
    row=await playback_router.create_history(HistoryCreate(file_id=vf.id,duration_played_sec=250,track_duration_sec=300,completed=True),db,user)
    assert row.file_id==vf.id
    assert db.scalar(select(func.count(playback.ListeningHistory.id)))==1

@pytest.mark.parametrize('check_only', [False, True])
def test_sealing_cli_wrong_key_commits_no_accounts(database, monkeypatch, capsys, check_only):
    import scripts.secure_tg_accounts as cli
    db, _, _, _ = database
    original = manager.encrypt_session_string('synthetic-session', 'synthetic-old-key')
    db.add_all([vfs.TGAccount(id=1,name='Synthetic legacy',session_string='legacy-session'),
                vfs.TGAccount(id=2,name='Synthetic encrypted',session_string=original)])
    db.commit()
    engine=db.get_bind()
    monkeypatch.setattr(cli,'SessionLocal',lambda:Session(engine))
    monkeypatch.setattr(cli.settings,'SESSION_SECRET','synthetic-wrong-key')
    monkeypatch.setattr('sys.argv',['secure_tg_accounts.py']+(['--check'] if check_only else []))
    assert cli.main()==2
    assert 'No changes committed' in capsys.readouterr().err
    db.expire_all()
    assert db.get(vfs.TGAccount,1).session_string=='legacy-session'
    assert db.get(vfs.TGAccount,2).session_string==original


@pytest.mark.asyncio
@pytest.mark.parametrize('complete', [False, True])
async def test_checked_encrypted_stream_caches_only_complete_plaintext(monkeypatch, complete):
    service=StreamService()
    vf=SimpleNamespace(id=73,name='Synthetic.mp3',size=52,mime_type='audio/mpeg',is_encrypted=True)
    service._header_cache[vf.id]=(b'S'*16,b'N'*12,1024)
    async def raw(**kwargs): yield b'unused synthetic encrypted data'
    async def decoded(**kwargs):
        yield b'12'
        if complete: yield b'34'
    monkeypatch.setattr('app.services.stream_service.fast_streamer.stream_file',raw)
    monkeypatch.setattr('app.services.stream_service.DecryptionService.decrypt_stream',decoded)
    monkeypatch.setattr('app.services.stream_service.ram_cache.get',AsyncMock(return_value=None))
    put=AsyncMock();monkeypatch.setattr('app.services.stream_service.ram_cache.put',put)
    response=await service.build_stream_response(request(),vf)
    if complete:
        assert b''.join([chunk async for chunk in response.body_iterator])==b'1234'
        put.assert_awaited_once_with(vf.id,b'1234')
    else:
        with pytest.raises(RuntimeError): [chunk async for chunk in response.body_iterator]
        put.assert_not_awaited()


@pytest.mark.asyncio
async def test_preflight_timeout_closes_source_and_returns_safe_503(monkeypatch):
    service=StreamService();closed=[];original_wait=asyncio.wait_for
    async def source(**kwargs):
        try:
            await asyncio.Event().wait();yield b'1234'
        finally:closed.append(True)
    async def short_wait(awaitable,timeout): return await original_wait(awaitable,0.01)
    monkeypatch.setattr('app.services.stream_service.fast_streamer.stream_file',source)
    monkeypatch.setattr('app.services.stream_service.ram_cache.get',AsyncMock(return_value=None))
    monkeypatch.setattr('app.services.stream_service.asyncio.wait_for',short_wait)
    with pytest.raises(HTTPException) as error:await service.build_stream_response(request(),track())
    assert error.value.status_code==503 and closed
