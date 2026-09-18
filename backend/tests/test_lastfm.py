"""
Last.fm signature + track meta parsing (no network).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.lastfm_service import _api_sig, parse_track_meta


def test_api_sig_stable():
    params = {
        "method": "track.scrobble",
        "artist": "Artist",
        "track": "Song",
        "api_key": "key",
        "sk": "session",
        "format": "json",
    }
    sig = _api_sig(params, "secret")
    assert isinstance(sig, str) and len(sig) == 32
    # Signature independent of dict order
    sig2 = _api_sig(dict(reversed(list(params.items()))), "secret")
    assert sig == sig2


def test_parse_filename_artist_title():
    artist, track, album = parse_track_meta("Daft Punk - Around the World.mp3")
    assert artist == "Daft Punk"
    assert track == "Around the World"
    assert album is None


def test_parse_hints_override():
    artist, track, album = parse_track_meta(
        "01 - intro.flac",
        artist_hint="Queen",
        track_hint="Bohemian Rhapsody",
        album_hint="A Night at the Opera",
    )
    assert artist == "Queen"
    assert track == "Bohemian Rhapsody"
    assert album == "A Night at the Opera"


def test_parse_plain_name():
    artist, track, album = parse_track_meta("mystery_track.mp3")
    assert artist == "Unknown"
    assert track == "mystery_track"
