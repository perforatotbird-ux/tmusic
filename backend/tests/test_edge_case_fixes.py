"""Boundary regressions: SQLite only; no live providers or secrets."""
import asyncio
import importlib.util
import json
import math
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import zipfile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.api import admin_router, playback_router, playlist_router, subsonic_router
from app.api.dependencies import get_current_user, get_admin_user
from app.core.database import get_db
from app.models.base import Base
from app.models import auth, playback, playlist, vfs
from app.schemas.playback import HistoryCreate
from app.schemas.playlist import PlaylistCreate
from app.services import lyrics_service
from app.services.playback_service import playback_service
from app.services.playlist_service import playlist_service

spec = importlib.util.spec_from_file_location('review_extract_logs', Path(__file__).resolve().parents[2] / 'scripts/extract_logs.py')
extract_logs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract_logs)


@pytest.fixture
def database(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        user = auth.AppUser(username='edge_admin', role='admin', is_active=True, password_hash='synthetic-placeholder')
        track = vfs.VFile(name='Synthetic.mp3', size=123, is_trash=False, mime_type='audio/mpeg')
        db.add_all([user, track]); db.commit(); db.refresh(user); db.refresh(track)
        app = FastAPI()
        for router in (admin_router.router, playback_router.router, playlist_router.router):
            app.include_router(router)
        def session():
            with sessions() as request_db:
                yield request_db
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_admin_user] = lambda: user
        monkeypatch.setattr('app.services.lastfm_service.update_now_playing', Mock())
        monkeypatch.setattr('app.services.lastfm_service.scrobble', Mock())
        yield db, user, track, TestClient(app, raise_server_exceptions=False)
    engine.dispose()


@pytest.mark.parametrize('name', ['', ' ', '\t\n', 'a'])
def test_username_rejected_after_trimming(name):
    with pytest.raises(ValidationError):
        admin_router.CreateUser(username=name, password='Synthetic!Password1')


def test_username_normalization_and_duplicate(database):
    db, user, track, client = database
    assert client.post('/api/admin/users', json={'username':'  ', 'password':'Synthetic!Password1'}).status_code == 422
    created = client.post('/api/admin/users', json={'username':'  new_user  ', 'password':'Synthetic!Password1'})
    assert created.status_code == 200 and created.json()['username'] == 'new_user'
    duplicate = client.post('/api/admin/users', json={'username':' new_user ', 'password':'Synthetic!Password1'})
    assert duplicate.status_code == 400
    assert db.scalar(select(func.count(auth.AppUser.id))) == 2


def test_username_creation_race_rolls_back_without_sql_details(monkeypatch):
    db = Mock()
    db.scalar.return_value = None
    db.flush.side_effect = IntegrityError('secret SQL details', {}, RuntimeError('unique failure'))
    async def worker(*args): return 'synthetic-placeholder'
    monkeypatch.setattr(admin_router, 'run_password_work', worker)
    with pytest.raises(HTTPException) as err:
        asyncio.run(admin_router.admin_create_user(
            admin_router.CreateUser(username='racing_user', password='Synthetic!Password1'),
            Request({'type':'http', 'method':'POST', 'path':'/api/admin/users', 'headers':[]}), db,
            SimpleNamespace(id=1, username='admin')))
    assert err.value.status_code == 400
    assert 'SQL' not in err.value.detail and 'unique' not in err.value.detail
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


@pytest.mark.parametrize('name', ['', ' ', '\t\n', 'x' * 121])
def test_playlist_rejected_in_schema_and_service(name):
    with pytest.raises(ValidationError): PlaylistCreate(name=name)
    db = Mock()
    with pytest.raises(HTTPException): playlist_service.create_playlist(db, 1, name)
    db.add.assert_not_called()


def test_playlist_http_trim_and_empty_rejection(database):
    db, user, track, client = database
    assert client.post('/api/playlists', json={'name':'   '}).status_code == 422
    created = client.post('/api/playlists', json={'name':'  Playlist Name  '})
    assert created.status_code == 200 and created.json()['name'] == 'Playlist Name'


@pytest.mark.parametrize('method', ['createPlaylist', 'updatePlaylist'])
def test_subsonic_rejects_whitespace_playlist_name(database, monkeypatch, method):
    db, user, track, client = database
    pl = playlist.Playlist(user_id=user.id, name='Unchanged')
    db.add(pl); db.commit(); db.refresh(pl)
    monkeypatch.setattr(subsonic_router, '_http_auth_user', lambda *args: user)
    req = Request({'type':'http', 'method':'GET', 'path':'/rest/'+method, 'query_string':b'', 'headers':[]})
    response = asyncio.run(subsonic_router._handle(method, req, db, {'name':'   ', 'playlistId':str(pl.id)} if method=='updatePlaylist' else {'name':'   '}, 'json'))
    payload = json.loads(response.body)['subsonic-response']
    assert payload['status'] == 'failed' and payload['error']['code'] == 10
    db.refresh(pl); assert pl.name == 'Unchanged'


def test_lyrics_filter_null_before_sort_and_validate_strings(monkeypatch):
    """Malformed search hits (null / str / int / wrong-typed fields) must be
    filtered out without raising, and the one valid node still wins.

    The lookup retries /api/get with fewer constraints before searching, so the
    stub is keyed on `path` rather than a positional side_effect list.
    """
    def fake_get(path, params, timeout):
        if path == 'get':
            return None
        return [None, 'bad', 1, {'plainLyrics':[]},
                {'syncedLyrics':'[00:01]Lyrics', 'artistName':{}, 'duration':'Infinity'}]

    monkeypatch.setattr(lyrics_service, '_get', fake_get)
    result = lyrics_service._lookup_upstream('Artist','Track',None,None,None,1)
    assert result['synced'] == '[00:01]Lyrics'
    assert result['artist'] is None and result['duration_sec'] is None


def test_lyrics_malformed_exact_response_falls_back(monkeypatch):
    """A non-dict /api/get answer must fall through to search, not blow up."""
    def fake_get(path, params, timeout):
        if path == 'get':
            return ['not-a-dict']
        return [{'plainLyrics':'Valid'}]

    monkeypatch.setattr(lyrics_service, '_get', fake_get)
    assert lyrics_service._lookup_upstream('Artist','Track',None,None,None,1)['plain'] == 'Valid'


@pytest.mark.parametrize('duration', ['Infinity', '-Infinity', 'NaN', float('inf'), float('nan'), -1, 86401])
@pytest.mark.parametrize('field', ['duration_played_sec','track_duration_sec'])
def test_history_rejects_nonfinite_negative_or_oversized(duration, field):
    with pytest.raises(ValidationError): HistoryCreate(file_id=1, **{field:duration})


@pytest.mark.parametrize('duration', [float('inf'), float('nan'), -1, 86401])
def test_history_service_rejects_invalid_values_without_write(duration):
    db = Mock()
    with pytest.raises(ValueError): playback_service.add_history(db, 1, 1, duration, None, False)
    db.add.assert_not_called()


def test_history_http_rejects_inf_without_commit_and_accepts_boundary(database):
    db, user, track, client = database
    assert client.post('/api/playback/history', json={'file_id':track.id,'duration_played_sec':'Infinity'}).status_code == 422
    assert db.scalar(select(func.count(playback.ListeningHistory.id))) == 0
    valid = client.post('/api/playback/history', json={'file_id':track.id,'duration_played_sec':86400,'track_duration_sec':86400})
    assert valid.status_code == 200 and valid.json()['duration_played_sec'] == 86400


def test_history_delivery_runs_off_the_event_loop(database, monkeypatch):
    db, user, track, client = database
    threads = {}
    original = playback_router.playback_service.add_history

    def record_add_history(*args, **kwargs):
        threads['endpoint'] = threading.get_ident()
        return original(*args, **kwargs)

    monkeypatch.setattr(playback_router.playback_service, 'add_history', record_add_history)
    monkeypatch.setattr(
        'app.services.lastfm_service.update_now_playing',
        lambda *args, **kwargs: threads.__setitem__('lastfm', threading.get_ident()),
    )
    response = client.post('/api/playback/history', json={'file_id': track.id, 'duration_played_sec': 1})
    assert response.status_code == 200
    assert 'lastfm' in threads and threads['lastfm'] != threads['endpoint']


def test_legacy_invalid_history_does_not_poison_analytics(database):
    db, user, track, client = database
    for value in [float('inf'),float('-inf'), -1, 86401, 25]:
        db.add(playback.ListeningHistory(user_id=user.id,file_id=track.id,duration_played_sec=value,track_duration_sec=float('inf'),completed=False))
    db.commit()
    response = client.get('/api/admin/analytics')
    assert response.status_code == 200
    result = response.json()
    assert result['totals']['seconds'] == 25
    assert all(row['seconds'] == 25 for name in ['top_tracks','top_users','daily'] for row in result[name])
    history = client.get('/api/playback/history')
    assert history.status_code == 200
    assert all(math.isfinite(row['duration_played_sec']) and row['track_duration_sec'] is None for row in history.json()['items'])
    # Read compatibility must not silently rewrite the original rows.
    assert any(math.isinf(row.duration_played_sec) for row in db.scalars(select(playback.ListeningHistory)))


def test_playback_history_updates_in_place_without_duplicates(database):
    db, user, track, client = database
    # 1. First event when track starts playing
    create_resp = client.post(
        '/api/playback/history',
        json={'file_id': track.id, 'duration_played_sec': 0.0, 'completed': False},
    )
    assert create_resp.status_code == 200
    initial_data = create_resp.json()
    history_id = initial_data['id']
    assert initial_data['duration_played_sec'] == 0.0
    assert initial_data['completed'] is False

    # 2. Subsequent flush on pause/skip/end passing history_id
    update_resp = client.post(
        '/api/playback/history',
        json={
            'history_id': history_id,
            'file_id': track.id,
            'duration_played_sec': 42.5,
            'track_duration_sec': 180.0,
            'completed': True,
        },
    )
    assert update_resp.status_code == 200
    updated_data = update_resp.json()
    assert updated_data['id'] == history_id
    assert updated_data['duration_played_sec'] == 42.5
    assert updated_data['track_duration_sec'] == 180.0
    assert updated_data['completed'] is True

    # 3. Verify exactly one row in DB for this track listen
    rows = db.scalars(select(playback.ListeningHistory).where(playback.ListeningHistory.file_id == track.id)).all()
    assert len(rows) == 1
    assert rows[0].id == history_id
    assert rows[0].duration_played_sec == 42.5
    assert rows[0].completed is True

    # 4. Verify admin analytics returns recent events with track and user info
    analytics_resp = client.get('/api/admin/analytics?days=7')
    assert analytics_resp.status_code == 200
    analytics_data = analytics_resp.json()
    assert 'recent' in analytics_data
    assert len(analytics_data['recent']) >= 1
    recent_event = analytics_data['recent'][0]
    assert recent_event['id'] == history_id
    assert recent_event['file_id'] == track.id
    assert recent_event['track_name'] == 'Synthetic.mp3'
    assert recent_event['duration_played_sec'] == 42.5
    assert recent_event['completed'] is True
    assert recent_event['username'] == 'edge_admin'


@pytest.mark.parametrize('directory_only', [False, True])
def test_empty_zip_reports_exit_without_traceback(tmp_path, capsys, directory_only):
    path = tmp_path / 'empty.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        if directory_only: archive.writestr('logs/', '')
    with pytest.raises(SystemExit) as err: extract_logs._read_zip(path, 'synthetic-password')
    assert err.value.code == 1
    assert 'Archive contains no files' in capsys.readouterr().err


def test_zip_skips_directory_before_first_file(tmp_path):
    path = tmp_path / 'logs.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('logs/', '')
        archive.writestr('logs/backend.log', 'synthetic content')
    assert extract_logs._read_zip(path, 'synthetic-password') == b'synthetic content'
