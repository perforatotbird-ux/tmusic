"""Prefetch must not consume the slots reserved for playback (PERF-003)."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.transcoding_service import (
    MAX_QUEUED_TRANSCODES,
    PLAYBACK_RESERVE_SLOTS,
    PRIORITY_PLAYING,
    PRIORITY_PREFETCH,
    TranscodePriorityQueue,
    TranscodingService,
    TranscodeStatus,
)


def _track(track_id):
    return SimpleNamespace(
        id=track_id,
        name=f"{track_id}.m4a",
        size=1000,
        mime_type="audio/mp4",
        is_encrypted=False,
        updated_at=datetime(2026, 1, 1),
    )


def _service(monkeypatch):
    service = TranscodingService()
    service._queue = TranscodePriorityQueue(maxsize=MAX_QUEUED_TRANSCODES)
    service._started = True
    monkeypatch.setattr(service, "get_status", lambda _file_id: TranscodeStatus.PENDING)
    return service


def test_prefetch_stops_at_reserved_watermark_and_playback_still_enqueues(monkeypatch):
    service = _service(monkeypatch)
    watermark = MAX_QUEUED_TRANSCODES - PLAYBACK_RESERVE_SLOTS

    for file_id in range(1, watermark + 1):
        service.request_transcode(_track(file_id), priority=PRIORITY_PREFETCH)

    with pytest.raises(HTTPException) as exc:
        service.request_transcode(_track(watermark + 1), priority=PRIORITY_PREFETCH)
    assert exc.value.status_code == 429

    service.request_transcode(_track(watermark + 2), priority=PRIORITY_PLAYING)
    assert service._queue.qsize() == watermark + 1
