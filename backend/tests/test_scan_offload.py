"""Last.fm calls in scan jobs must leave the event loop (PERF-001)."""
import asyncio
import threading
from types import SimpleNamespace

from app.services import scan_service


class _FakeDb:
    def commit(self):
        pass

    def refresh(self, _obj):
        pass


def test_artist_scan_calls_lastfm_in_worker_thread(monkeypatch):
    seen = {}

    def fake_get_artist_info(name, force=False):
        seen["thread"] = threading.current_thread()
        return {"summary": "", "mbid": None, "image_url": None, "tags": [], "similar": []}

    artist = SimpleNamespace(
        id=1,
        display_name="Synthetic Artist",
        bio=None,
        mbid=None,
        image_url=None,
        custom_image_key=None,
        enriched_at=None,
        updated_at=None,
    )
    monkeypatch.setattr(scan_service.lastfm_service, "metadata_ready", lambda: True)
    monkeypatch.setattr(scan_service.lastfm_service, "get_artist_info", fake_get_artist_info)
    monkeypatch.setattr(scan_service.library_service, "get_artist", lambda db, aid: artist)
    monkeypatch.setattr(scan_service.library_service, "list_artist_albums", lambda *a, **k: [])

    result = asyncio.run(scan_service.scan_artist_in_db(_FakeDb(), 1, lambda *a, **k: None))

    assert result["ok"] is True
    assert seen["thread"] is not threading.main_thread()
