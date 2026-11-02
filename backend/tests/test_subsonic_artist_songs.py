"""artist_songs must be one bounded query, not one per album (PERF-005)."""
from datetime import datetime
from types import SimpleNamespace

from app.services import subsonic_service


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows
        self.executed = 0

    def execute(self, _stmt):
        self.executed += 1
        return _FakeResult(self._rows)


def _track(track_id):
    return SimpleNamespace(
        id=track_id,
        name=f"0{track_id} - Track.mp3",
        size=100,
        mime_type="audio/mpeg",
        created_at=datetime(2026, 1, 1),
    )


def test_artist_songs_uses_one_query_and_resets_track_numbers(monkeypatch):
    rows = [
        (_track(1), 11, "Album A", 7, 2020),
        (_track(2), 11, "Album A", 7, 2020),
        (_track(3), 12, "Album B", 7, 2021),
    ]
    db = _FakeDb(rows)
    artist = SimpleNamespace(id=7, display_name="Artist")

    songs = subsonic_service.artist_songs(db, artist, limit=3)

    assert db.executed == 1
    assert [song["track"] for song in songs] == [1, 2, 1]
    assert [song["album"] for song in songs] == ["Album A", "Album A", "Album B"]
