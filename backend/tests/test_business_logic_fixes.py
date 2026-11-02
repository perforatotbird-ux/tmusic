"""Business-logic regressions. Synthetic fixtures; no live DB/TG/ffmpeg."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree
import struct

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.api import admin_catalog_router, share_router, stream_router, subsonic_router
from app.core.config import settings
from app.models.base import Base
from app.models import auth, music, vfs
from app.repositories.album_repository import AlbumRepository
from app.services import transcoding_service as transcodes, vfs_import_service as imports
from app.services.decryption_service import DecryptionService
from app.services.music_library_service import CatalogAdminService
from app.services.playback_service import playback_service
from app.services.stream_service import StreamService, stream_service
from app.services.subsonic_response import dict_to_subsonic_xml, ok_envelope
from app.services.transcoding_service import TranscodingService, TranscodePriorityQueue, TranscodeStatus


def snapshot(parents, closures=()):
    return SimpleNamespace(folders={i: {'id': i, 'parent_id': parent, 'name': f'Folder{i}'} for i, parent in parents.items()},
        closures=list(closures), path_by_id={}, depth_by_id={})


@pytest.mark.parametrize('parents', [{1: 1}, {1: 2, 2: 1}, {1: None, 2: 3, 3: 2}])
@pytest.mark.parametrize('closures', [[], [(1, 1, 0), (1, 2, 1)]])
def test_source_parent_cycles_are_rejected_even_with_closure_rows(parents, closures):
    with pytest.raises(imports.VfsImportError, match='цикл'):
        imports._index_source(snapshot(parents, closures))


def test_source_missing_parent_is_rejected_and_depth_is_bounded():
    with pytest.raises(imports.VfsImportError, match='родительская'):
        imports._index_source(snapshot({1: 999}))
    valid = snapshot({i: i-1 if i > 1 else None for i in reversed(range(1, 257))})
    imports._index_source(valid)
    assert valid.depth_by_id[256] == 255
    assert valid.path_by_id[256].startswith('Folder1/Folder2/')
    with pytest.raises(imports.VfsImportError, match='256'):
        imports._index_source(snapshot({i: i-1 if i > 1 else None for i in range(1, 258)}))


def test_parent_links_not_inconsistent_closure_rows_define_paths():
    source = snapshot({1: None, 2: 1, 3: 2}, [(3, 1, 99)])
    imports._index_source(source)
    assert source.path_by_id == {1: 'Folder1', 2: 'Folder1/Folder2', 3: 'Folder1/Folder2/Folder3'}
    assert source.depth_by_id == {1: 0, 2: 1, 3: 2}


def queued_service(monkeypatch):
    service = TranscodingService(); service._started = True
    service._queue = TranscodePriorityQueue(maxsize=3)
    monkeypatch.setattr(service, '_check_disk_cache', lambda _: None)
    tracks = [SimpleNamespace(id=i, name=f'Synthetic{i}.flac', size=123, updated_at=datetime(2026,1,1),
        is_encrypted=False, mime_type='audio/flac') for i in (1,2,3)]
    for track in tracks: service.request_transcode(track, priority=1)
    return service, tracks


@pytest.mark.asyncio
async def test_priority_promotion_at_full_capacity_preserves_join_and_runs_once(monkeypatch):
    service, tracks = queued_service(monkeypatch)
    assert service._queue.full()
    unfinished = service._queue._unfinished_tasks
    service.request_transcode(tracks[2], priority=0)
    service.request_transcode(tracks[2], priority=0)
    assert service._queue.qsize() == 3 and service._queue._unfinished_tasks == unfinished
    monkeypatch.setattr(transcodes, 'get_cached_transcode_path', lambda _: None)
    played = []
    async def transcode(job): played.append(job.file_id); job.status = TranscodeStatus.READY
    monkeypatch.setattr(service, '_transcode_track', transcode)
    service._workers = [asyncio.create_task(service._worker('synthetic'))]
    try:
        await asyncio.wait_for(service._queue.join(), 1)
        assert played[0] == 3 and sorted(played) == [1, 2, 3]
    finally: await service.shutdown()


def test_queue_promotion_preserves_fifo_and_does_not_reenqueue_acquired_job():
    queue = TranscodePriorityQueue(maxsize=2)
    queue.put_nowait((1, 1, 1)); queue.put_nowait((1, 2, 2))
    assert queue.promote(2, 0)
    assert queue.get_nowait() == (0, 2, 2)
    assert not queue.promote(2, 0)
    queue.task_done()
    assert queue.get_nowait() == (1, 1, 1)
    queue.task_done()
    assert queue._unfinished_tasks == 0


def test_failed_status_remains_failed_without_cache_but_ready_cache_wins(monkeypatch):
    service, _ = queued_service(monkeypatch)
    service._jobs[1].status = TranscodeStatus.FAILED
    assert service.get_status(1) == TranscodeStatus.FAILED
    monkeypatch.setattr(service, '_check_disk_cache', lambda _: {'cached_path': 'synthetic'})
    assert service.get_status(1) == TranscodeStatus.READY


@pytest.fixture
def database():
    engine = create_engine('sqlite:///:memory:')
    @event.listens_for(engine, 'connect')
    def fk(connection, record): connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        user = auth.AppUser(username='synthetic', password_hash='placeholder', is_active=True, role='admin')
        root = vfs.Folder(name='Root'); db.add_all([user, root]); db.flush()
        artist = music.MusicArtist(folder_id=root.id, display_name='Synthetic', search_name='synthetic', kind='artist')
        db.add(artist); db.commit()
        yield db, user, artist
    engine.dispose()


@pytest.mark.asyncio
async def test_search_before_pagination_and_literal_like_wildcards(database):
    db, user, artist = database
    for title, year in [('Alpha',2000), ('Wanted First',2001), ('Wanted Second',2002), ('100%_Real',2003)]:
        folder=vfs.Folder(name=title); db.add(folder); db.flush()
        db.add(music.MusicAlbum(folder_id=folder.id, artist_id=artist.id, title=title, year=year))
    db.commit()
    first = await admin_catalog_router.catalog_albums(search='wanted', artist_id=artist.id, limit=1, offset=0, db=db, user=user)
    second = await admin_catalog_router.catalog_albums(search='wanted', artist_id=artist.id, limit=1, offset=1, db=db, user=user)
    assert [r.title for r in first] == ['Wanted First']
    assert [r.title for r in second] == ['Wanted Second']
    assert [r.title for r in AlbumRepository.list_by_artist_id(db, artist.id, search='%_', limit=1)] == ['100%_Real']


def test_delete_card_preserves_backing_folder_used_by_children(database):
    db, user, artist = database
    folder_id, artist_id = artist.folder_id, artist.id
    child=vfs.Folder(name='Child', parent_id=folder_id); db.add(child); db.commit(); child_id=child.id
    assert CatalogAdminService.delete_artist(db, artist_id)['ok']
    assert db.get(music.MusicArtist, artist_id) is None
    assert db.get(vfs.Folder, folder_id) is not None
    assert db.get(vfs.Folder, child_id).parent_id == folder_id


def test_delete_unused_card_removes_only_unused_backing_folder(database):
    db, user, artist = database
    folder_id=artist.folder_id
    db.add(vfs.FolderClosure(ancestor_id=folder_id, descendant_id=folder_id, depth=0)); db.commit()
    CatalogAdminService.delete_artist(db, artist.id)
    assert db.get(vfs.Folder, folder_id) is None


@pytest.mark.parametrize('text', ['AC & DC <Live>', '"Quoted" & \'single\'', 'Literal &amp; text', 'Line\nBreak'])
def test_xml_attribute_roundtrip_escapes_once(text):
    payload=ok_envelope('song', {'id':'fl-1','title':text,'isDir':False,'empty':None})
    root=ElementTree.fromstring(dict_to_subsonic_xml(payload))
    assert root.find('song').get('title') == text
    assert root.find('song').get('isDir') == 'false'
    assert root.find('song').get('empty') == ''


def test_favorite_count_filters_match_every_page(database):
    db, user, artist = database
    tracks=[vfs.VFile(name=f'Synthetic{i}', mime_type=mime, is_trash=trash, size=123)
        for i, (mime,trash) in enumerate([('audio/mpeg',False),('audio/mpeg',False),('image/png',False),('audio/mpeg',True)])]
    db.add_all(tracks); db.commit()
    for track in tracks: playback_service.set_favorite(db, user.id, track.id, True)
    for offset, expected in [(0,1),(1,1),(2,0)]:
        rows,total=playback_service.list_favorites(db,user.id,limit=1,offset=offset)
        assert len(rows)==expected and total==2


def request(range_header=None):
    headers=[(b'range',range_header.encode())] if range_header else []
    return Request({'type':'http','method':'GET','path':'/','headers':headers})


@pytest.mark.asyncio
@pytest.mark.parametrize('entry', ['main','share','subsonic'])
@pytest.mark.parametrize('range_header,expected_status,expected_length', [(None,200,5000),('bytes=100-199',206,100),('bytes=-25',206,25),('not-a-range',200,5000)])
async def test_cached_mp3_headers_body_and_ranges_agree(monkeypatch,tmp_path,entry,range_header,expected_status,expected_length):
    path=tmp_path/'cache.mp3'; payload=b'M'*5000; path.write_bytes(payload)
    track=SimpleNamespace(id=1,name='Synthetic.flac',size=10000,is_encrypted=False,mime_type='audio/flac')
    monkeypatch.setattr(transcodes.transcoding_service,'get_result',lambda _: {'cached_path':str(path),'mp3_size':99999})
    monkeypatch.setattr(stream_service,'get_track_or_404',lambda *args:track)
    enqueues=[]; monkeypatch.setattr(transcodes.transcoding_service,'request_transcode',lambda *args,**kwargs:enqueues.append(1))
    async def work(*args): return SimpleNamespace(id=1)
    monkeypatch.setattr(subsonic_router,'run_password_work',work)
    req=request(range_header)
    if entry=='main': response=await stream_router.stream_audio_file(1,req,None,SimpleNamespace(id=1))
    elif entry=='share': response=await share_router._stream_track_response(req,track)
    else: response=await subsonic_router._handle('stream',req,None,{'id':'fl-1'},'json')
    body=b''.join([chunk async for chunk in response.body_iterator])
    assert response.status_code==expected_status and len(body)==expected_length
    assert int(response.headers['content-length'])==len(body)
    assert response.headers['content-type'].startswith('audio/mpeg')
    assert '.mp3' in response.headers['content-disposition']
    assert not enqueues
    if expected_status==206: assert response.headers['content-range'].endswith('/5000')
    else: assert 'content-range' not in response.headers
    if range_header=='bytes=100-199': assert body==payload[100:200]


@pytest.mark.asyncio
async def test_range_416_uses_selected_mp3_size(monkeypatch,tmp_path):
    path=tmp_path/'cache.mp3'; path.write_bytes(b'M'*5000)
    track=SimpleNamespace(id=1,name='Synthetic.flac',size=10000,is_encrypted=False,mime_type='audio/flac')
    monkeypatch.setattr(transcodes.transcoding_service,'get_result',lambda _: {'cached_path':str(path)})
    with pytest.raises(HTTPException) as err: await stream_service.build_stream_response(request('bytes=5000-'),track)
    assert err.value.status_code==416 and err.value.headers['Content-Range']=='bytes */5000'


@pytest.mark.asyncio
async def test_late_mp3_cache_cannot_replace_selected_raw_body(monkeypatch,tmp_path):
    path=tmp_path/'cache.mp3'; path.write_bytes(b'M'*5)
    track=SimpleNamespace(id=1,name='Synthetic.flac',size=10,is_encrypted=False,mime_type='audio/flac')
    result_calls=[]
    def result(*args):
        result_calls.append(1)
        return None if len(result_calls)==1 else {'cached_path':str(path)}
    monkeypatch.setattr(transcodes.transcoding_service,'get_result',result)
    async def raw(source,offset=0,limit=None): yield b'original!!'[offset:offset+limit if limit is not None else None]
    monkeypatch.setattr('app.services.stream_service.fast_streamer.stream_file',raw)
    monkeypatch.setattr('app.services.stream_service.ram_cache.get',AsyncMock(return_value=None))
    response=await stream_service.build_stream_response(request(),track)
    body=b''.join([chunk async for chunk in response.body_iterator])
    assert body==b'original!!' and response.headers['content-type'].startswith('audio/flac')
    assert len(result_calls)==1


@pytest.mark.asyncio
async def test_non_default_encryption_header_resolves_size_and_seek_before_headers(monkeypatch):
    service=StreamService(); block_size=512*1024
    key=b'A'*32; salt=b'B'*16; nonce=b'C'*12
    plaintext=b'P'*(3*block_size)
    file_key=DecryptionService.derive_file_key(key,salt); aes=AESGCM(file_key)
    encrypted=bytearray(salt+nonce+struct.pack('<I',block_size))
    for i in range(3):
        block_nonce=((int.from_bytes(nonce,'big')+i)%(1<<96)).to_bytes(12,'big')
        encrypted.extend(aes.encrypt(block_nonce,plaintext[i*block_size:(i+1)*block_size],None))
    track=SimpleNamespace(id=1,name='Synthetic.mp3',size=len(encrypted),is_encrypted=True,mime_type='audio/mpeg')
    monkeypatch.setattr(type(settings),'get_master_keys',lambda self:[key])
    monkeypatch.setattr('app.services.stream_service.ram_cache.get',AsyncMock(return_value=None))
    calls=[]; closed=[]
    async def raw(source,offset=0,limit=None):
        calls.append((offset,limit))
        data=encrypted[offset:offset+limit if limit is not None else None]
        try:
            for i in range(0,len(data),8192): yield bytes(data[i:i+8192])
        finally: closed.append((offset,limit))
    monkeypatch.setattr('app.services.stream_service.fast_streamer.stream_file',raw)
    assert service.get_track_size(track) is None
    assert await service.get_plain_track_size(track)==len(plaintext)
    assert service.get_track_size(track)==len(plaintext)
    response=await service.build_stream_response(request('bytes=524280-524300'),track)
    body=b''.join([chunk async for chunk in response.body_iterator])
    assert response.headers['content-range']==f'bytes 524280-524300/{len(plaintext)}'
    assert body==plaintext[524280:524301]
    assert calls.count((0,32))==1 and (0,32) in closed


@pytest.mark.asyncio
async def test_short_encryption_header_fails_before_response(monkeypatch):
    service=StreamService()
    track=SimpleNamespace(id=1,name='Synthetic.mp3',size=1000,is_encrypted=True,mime_type='audio/mpeg')
    monkeypatch.setattr('app.services.stream_service.ram_cache.get',AsyncMock(return_value=None))
    async def raw(**kwargs): yield b'short'
    monkeypatch.setattr('app.services.stream_service.fast_streamer.stream_file',raw)
    with pytest.raises(HTTPException) as err: await service.build_stream_response(request(),track)
    assert err.value.status_code==503 and 'header' not in err.value.detail.lower()
