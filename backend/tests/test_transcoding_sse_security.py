import asyncio
from types import SimpleNamespace

import pytest

from app.services.transcoding_service import TranscodingService


def test_sse_notifications_are_scoped_to_requesting_user():
    service = TranscodingService()
    user_one = service.subscribe(1)
    user_two = service.subscribe(2)

    service._notify({"event": "transcode_started", "file_id": 42}, {1})

    assert user_one.get_nowait()["file_id"] == 42
    with pytest.raises(asyncio.QueueEmpty):
        user_two.get_nowait()


def test_sse_connections_are_limited_per_user():
    service = TranscodingService()
    service.subscribe(1, max_connections=2)
    service.subscribe(1, max_connections=2)

    with pytest.raises(ValueError, match="too_many_sse_connections"):
        service.subscribe(1, max_connections=2)


def test_existing_job_collects_all_requesting_users(monkeypatch):
    service = TranscodingService()
    service._started = True
    service._queue = asyncio.PriorityQueue()
    from app.services.transcoding_service import TranscodeStatus

    monkeypatch.setattr(service, "get_status", lambda file_id: TranscodeStatus.PENDING)
    track = SimpleNamespace(
        id=42,
        name="song.flac",
        size=123,
        updated_at=None,
        is_encrypted=False,
        mime_type="audio/flac",
    )

    service.request_transcode(track, requester_user_id=1)
    service.request_transcode(track, requester_user_id=2)

    assert service._jobs[42].requester_user_ids == {1, 2}