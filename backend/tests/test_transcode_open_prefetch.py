"""Открытие сборника не должно ждать транскодинг (2026-10-16).

Регрессия: `GET /api/stream/tracks/{folder_id}` для папки из 454 треков,
требующих транскодинга, отвечал 15.5 с (замер на живой БД). Причина — обход
ВСЕХ `*.meta.json` в кеше на каждый трек, причём дважды (get_status + get_result)
и синхронно в обработчике. Плюс сам список треков отдавался только после
завершения этой работы, поэтому открытие сборника «висело» вместе с идущим
транскодингом.

Здесь закреплены два контракта:
  1. индекс манифестов — каталог сканируется один раз на TTL, а не на трек;
  2. список треков уходит клиенту сразу, транскодинг уходит в фон.
"""
import json
import pathlib
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.services.transcoding_service import (
    PRIORITY_PREFETCH,
    TranscodePriorityQueue,
    TranscodingService,
    TranscodeStatus,
)


def fake_track(file_id, name, mime_type='audio/mp4'):
    return SimpleNamespace(
        id=file_id, name=name, size=4096, updated_at=None,
        is_encrypted=False, mime_type=mime_type,
    )


def seed_cached(tmp_path, file_id, cache_key=None, created_at=1.0):
    """Готовый транскод на диске: mp3 >= 1 KiB + манифест."""
    key = cache_key or f'{file_id:032d}'
    (tmp_path / f'{key}.mp3').write_bytes(b'ID3' + b'x' * 2048)
    (tmp_path / f'{key}.meta.json').write_text(json.dumps({
        'status': 'ready', 'cache_key': key, 'file_id': file_id,
        'mp3_size': 2051, 'duration': 12.0, 'created_at': created_at,
    }), encoding='utf-8')
    return key


def started_service(tmp_path, monkeypatch, maxsize=100):
    monkeypatch.setattr(settings, 'TRANSCODE_CACHE_DIR', tmp_path)
    service = TranscodingService()
    service._started = True
    service._queue = TranscodePriorityQueue(maxsize=maxsize)
    return service


# ─── 1. Индекс манифестов ──────────────────────────────────────────────────────

def test_manifest_index_scans_cache_directory_once(monkeypatch, tmp_path):
    """25 треков в списке = 1 сканирование каталога, а не 25 (и не 50)."""
    for file_id in range(1, 26):
        seed_cached(tmp_path, file_id)
    service = started_service(tmp_path, monkeypatch)

    calls = []
    original = pathlib.Path.glob

    def counting_glob(self, pattern, *args, **kwargs):
        calls.append(pattern)
        return original(self, pattern, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, 'glob', counting_glob)

    for file_id in range(1, 26):
        assert service.get_status(file_id) == TranscodeStatus.READY

    assert calls.count('*.meta.json') == 1, f'каталог сканировался {calls.count("*.meta.json")} раз'


def test_manifest_index_prefers_newest_manifest_for_same_file_id(monkeypatch, tmp_path):
    """Трек перезалили → в кеше два ключа; отдавать надо свежий, а не первый по обходу."""
    old_key = seed_cached(tmp_path, 7, cache_key='a' * 32, created_at=1.0)
    new_key = seed_cached(tmp_path, 7, cache_key='b' * 32, created_at=2.0)
    service = started_service(tmp_path, monkeypatch)

    assert service.get_result(7)['cache_key'] == new_key
    assert old_key != new_key


def test_indexed_manifest_without_mp3_is_not_reported_ready(monkeypatch, tmp_path):
    """Манифест остался, файл вытеснен → это не READY, и запись уходит из индекса."""
    key = seed_cached(tmp_path, 7)
    (tmp_path / f'{key}.mp3').unlink()
    service = started_service(tmp_path, monkeypatch)

    assert service.get_status(7) != TranscodeStatus.READY
    assert service.get_result(7) is None
    assert 7 not in service._disk_index


def test_corrupt_manifest_does_not_break_the_index(monkeypatch, tmp_path):
    seed_cached(tmp_path, 1)
    (tmp_path / f'{"c" * 32}.meta.json').write_text('{not json', encoding='utf-8')
    (tmp_path / f'{"c" * 32}.mp3').write_bytes(b'ID3' + b'x' * 2048)
    service = started_service(tmp_path, monkeypatch)

    assert service.get_status(1) == TranscodeStatus.READY
    assert service.get_status(2) == TranscodeStatus.PENDING


# ─── 2. Фоновая предзагрузка коллекции ────────────────────────────────────────

def test_prefetch_collection_queues_only_needed_tracks_and_respects_limit(monkeypatch, tmp_path):
    service = started_service(tmp_path, monkeypatch)
    seed_cached(tmp_path, 9)  # уже готов — не должен занимать место в лимите
    tracks = [
        fake_track(1, '01.mp3', 'audio/mpeg'),   # не нужен транскодинг
        fake_track(9, '02.m4a'),                 # уже в кеше
        fake_track(2, '03.m4a'),                 # нужен
        fake_track(3, '04.flac', 'audio/flac'),  # нужен
        fake_track(4, '05.m4a'),                 # нужен
    ]

    assert service.prefetch_collection(tracks, requester_user_id=1, limit=2) == 2

    queued = []
    while not service._queue.empty():
        queued.append(service._queue.get_nowait())
    assert [entry[2] for entry in queued] == [2, 3]
    assert all(entry[0] == PRIORITY_PREFETCH for entry in queued)
    assert service._jobs[2].background is True
    assert 1 not in service._jobs and 9 not in service._jobs


def test_prefetch_collection_does_not_duplicate_queued_or_active_tracks(monkeypatch, tmp_path):
    service = started_service(tmp_path, monkeypatch)
    tracks = [fake_track(2, '03.m4a'), fake_track(3, '04.m4a')]

    assert service.prefetch_collection(tracks, requester_user_id=1, limit=10) == 2
    assert service.prefetch_collection(tracks, requester_user_id=1, limit=10) == 0
    assert service._queue.qsize() == 2


def test_prefetch_collection_survives_a_full_queue(monkeypatch, tmp_path):
    """Переполнение очереди обрывает залп, но не роняет открытие сборника."""
    service = started_service(tmp_path, monkeypatch, maxsize=1)
    service._queue.put_nowait((PRIORITY_PREFETCH, 0.0, 999))

    assert service.prefetch_collection([fake_track(2, '03.m4a')], requester_user_id=1) == 0


def test_prefetch_collection_survives_broken_track_rows(monkeypatch, tmp_path):
    """Строка без mime_type/name не должна превращаться в 500 на списке треков."""
    service = started_service(tmp_path, monkeypatch)
    broken = SimpleNamespace(id=5, size=1, updated_at=None, is_encrypted=False)

    assert service.prefetch_collection([broken, fake_track(2, '03.m4a')], requester_user_id=1) == 1


def test_prefetch_collection_is_disabled_when_service_is_not_started(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'TRANSCODE_CACHE_DIR', tmp_path)
    service = TranscodingService()

    assert service.prefetch_collection([fake_track(2, '03.m4a')], requester_user_id=1) == 0


# ─── 3. Контракт с клиентом: фон не спамит тостами ────────────────────────────

def test_background_prefetch_events_are_marked_for_the_client(monkeypatch, tmp_path):
    """SSE помечает фоновые задачи, иначе плеер показал бы тост на каждый трек сборника."""
    service = started_service(tmp_path, monkeypatch)
    client_queue = service.subscribe(1)

    service.prefetch_collection([fake_track(5, 'x.m4a')], requester_user_id=1)

    event = client_queue.get_nowait()
    assert event['event'] == 'transcode_queued'
    assert event['background'] is True


# ─── 4. Обработчик отдаёт список до постановки задач в фон ────────────────────

def test_folder_tracks_endpoint_answers_before_background_prefetch(monkeypatch):
    from app.api import stream_router
    from app.api.dependencies import get_current_user
    from app.core.database import get_db

    order = []
    vfiles = [fake_track(1, 'a.m4a')]
    monkeypatch.setattr(stream_router.stream_service, 'list_folder_tracks', lambda *a, **k: vfiles)
    monkeypatch.setattr(
        stream_router.stream_service, 'build_track_responses',
        lambda db, files: (order.append('response'), [])[1],
    )
    monkeypatch.setattr(
        stream_router.transcoding_service, 'prefetch_collection',
        lambda tracks, requester_user_id=None: order.append('prefetch'),
    )

    app = FastAPI()
    app.include_router(stream_router.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_db] = lambda: Mock()

    with TestClient(app) as client:
        response = client.get('/api/stream/tracks/1')

    assert response.status_code == 200
    assert order == ['response', 'prefetch']
