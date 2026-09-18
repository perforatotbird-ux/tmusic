"""
T-Music Last.fm scrobbling (optional enrichment).

Requires in music.env:
  LASTFM_API_KEY, LASTFM_API_SECRET, LASTFM_SESSION_KEY
Optional: LASTFM_USERNAME

Never logs secrets. Network failures are swallowed — playback must not depend
on Last.fm availability.
"""
import hashlib
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import settings

logger = logging.getLogger("tmusic.scrobble")

API_URL = "https://ws.audioscrobbler.com/2.0/"


def is_configured() -> bool:
    return bool(
        settings.LASTFM_ENABLED
        and settings.LASTFM_API_KEY
        and settings.LASTFM_API_SECRET
        and settings.LASTFM_SESSION_KEY
    )


def _api_sig(params: Dict[str, str], secret: str) -> str:
    """Last.fm method signature: md5(concat sorted key+value + secret)."""
    raw = "".join(f"{k}{params[k]}" for k in sorted(params) if k != "api_sig")
    return hashlib.md5((raw + secret).encode("utf-8")).hexdigest()


def _post(params: Dict[str, Any]) -> Optional[dict]:
    if not is_configured():
        return None
    payload = {
        "api_key": settings.LASTFM_API_KEY,
        "sk": settings.LASTFM_SESSION_KEY,
        "format": "json",
        **params,
    }
    payload["api_sig"] = _api_sig({k: str(v) for k, v in payload.items()}, settings.LASTFM_API_SECRET or "")
    data = urlencode(payload).encode("utf-8")
    req = Request(API_URL, data=data, method="POST", headers={"User-Agent": "T-Music/1.0"})
    try:
        with urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                import json

                return json.loads(body) if body else None
            except Exception:
                return None
    except Exception as e:
        logger.warning("Last.fm request failed: %s", e)
        return None


def parse_track_meta(
    file_name: Optional[str],
    artist_hint: Optional[str] = None,
    track_hint: Optional[str] = None,
    album_hint: Optional[str] = None,
) -> tuple[str, str, Optional[str]]:
    """Best-effort artist/title from 'Artist - Title.mp3' or explicit hints."""
    artist = (artist_hint or "").strip()
    track = (track_hint or "").strip()
    album = (album_hint or "").strip() or None
    base = (file_name or "").rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    base = base.strip()
    if not track:
        if " - " in base:
            left, right = base.split(" - ", 1)
            if not artist:
                artist = left.strip()
            track = right.strip()
        else:
            track = base or "Unknown"
    if not artist:
        artist = "Unknown"
    return artist, track, album


def update_now_playing(artist: str, track: str, album: Optional[str] = None) -> None:
    if not artist or not track:
        return
    params: Dict[str, Any] = {
        "method": "track.updateNowPlaying",
        "artist": artist,
        "track": track,
    }
    if album:
        params["album"] = album
    _post(params)


def scrobble(
    artist: str,
    track: str,
    album: Optional[str] = None,
    timestamp: Optional[int] = None,
) -> None:
    if not artist or not track or artist == "Unknown" and not track:
        return
    if not is_configured():
        return
    params: Dict[str, Any] = {
        "method": "track.scrobble",
        "artist": artist,
        "track": track,
        "timestamp": int(timestamp or time.time()),
    }
    if album:
        params["album"] = album
    result = _post(params)
    if result is None:
        logger.debug("Scrobble skipped/failed for %s — %s", artist, track)
    else:
        logger.info("Scrobbled: %s — %s", artist, track)
