"""
T-Music Last.fm integration (metadata + optional scrobbling).

music.env:
  LASTFM_API_KEY
  LASTFM_SHARED_SECRET (or LASTFM_API_SECRET) — metadata + signed write calls
  LASTFM_SESSION_KEY — required only for scrobble / now-playing
  LASTFM_USERNAME — optional

Read methods (artist.getInfo, track.getInfo) need api_key only.
Write methods need api_key + secret + session key.

Never logs secrets. Network failures are swallowed.
"""
import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import settings

logger = logging.getLogger("tmusic.scrobble")

API_URL = "https://ws.audioscrobbler.com/2.0/"


def _secret() -> str:
    return settings.lastfm_secret or ""


def metadata_ready() -> bool:
    return bool(settings.lastfm_metadata_ready)


def scrobble_ready() -> bool:
    return bool(settings.lastfm_scrobble_ready)


def is_configured() -> bool:
    """Write-path readiness (scrobble/now-playing)."""
    return scrobble_ready()


def _api_sig(params: Dict[str, str], secret: str) -> str:
    """Last.fm method signature: md5(concat sorted key+value + secret)."""
    raw = "".join(f"{k}{params[k]}" for k in sorted(params) if k != "api_sig")
    return hashlib.md5((raw + secret).encode("utf-8")).hexdigest()


def _request(params: Dict[str, Any], signed: bool = False, method: str = "GET") -> Optional[dict]:
    if not settings.LASTFM_ENABLED or not settings.LASTFM_API_KEY:
        return None

    payload: Dict[str, Any] = {
        "api_key": settings.LASTFM_API_KEY,
        "format": "json",
        **params,
    }
    if signed:
        if not settings.LASTFM_SESSION_KEY or not _secret():
            return None
        payload["sk"] = settings.LASTFM_SESSION_KEY
        payload["api_sig"] = _api_sig({k: str(v) for k, v in payload.items()}, _secret())

    body = urlencode(payload).encode("utf-8")
    if method.upper() == "GET":
        url = f"{API_URL}?{urlencode(payload)}"
        req = Request(url, method="GET", headers={"User-Agent": "T-Music/1.0"})
    else:
        req = Request(
            API_URL,
            data=body,
            method="POST",
            headers={"User-Agent": "T-Music/1.0"},
        )
    try:
        with urlopen(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else None
    except Exception as e:
        logger.warning("Last.fm request failed: %s", e)
        return None


def _post(params: Dict[str, Any]) -> Optional[dict]:
    return _request(params, signed=True, method="POST")


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


def get_artist_info(artist: str) -> Optional[dict]:
    """artist.getInfo — bio, similar, tags. Uses api_key only."""
    if not artist or not metadata_ready():
        return None
    data = _request({"method": "artist.getInfo", "artist": artist}, signed=False, method="GET")
    if not data:
        return None
    node = data.get("artist") or {}
    bio = node.get("bio") or {}
    summary = bio.get("summary") or ""
    # Strip simple HTML from Last.fm summary
    if "<" in summary:
        out = []
        skip = False
        for ch in summary:
            if ch == "<":
                skip = True
            elif ch == ">":
                skip = False
            elif not skip:
                out.append(ch)
        summary = "".join(out).strip()
    similar = []
    for s in (node.get("similar") or {}).get("artist", []) or []:
        if isinstance(s, dict) and s.get("name"):
            similar.append(s["name"])
    tags = []
    for t in (node.get("tags") or {}).get("tag", []) or []:
        if isinstance(t, dict) and t.get("name"):
            tags.append(t["name"])
    return {
        "name": node.get("name") or artist,
        "mbid": node.get("mbid") or None,
        "url": node.get("url") or None,
        "summary": summary,
        "similar": similar[:8],
        "tags": tags[:10],
        "image_url": next(
            (img.get("#text") for img in (node.get("image") or []) if isinstance(img, dict) and img.get("#text")),
            None,
        ),
    }


def get_track_info(artist: str, track: str, album: Optional[str] = None) -> Optional[dict]:
    """track.getInfo — duration, album, tags."""
    if not artist or not track or not metadata_ready():
        return None
    params: Dict[str, Any] = {"method": "track.getInfo", "artist": artist, "track": track}
    if album:
        params["album"] = album
    data = _request(params, signed=False, method="GET")
    if not data:
        return None
    node = data.get("track") or {}
    album_node = node.get("album") or {}
    duration_raw = node.get("duration")
    try:
        duration = int(duration_raw) // 1000 if duration_raw not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        duration = None
    return {
        "name": node.get("name") or track,
        "artist": (node.get("artist") or {}).get("name") or artist,
        "album": album_node.get("title") or album,
        "duration_sec": duration,
        "mbid": node.get("mbid") or None,
        "url": node.get("url") or None,
        "image_url": next(
            (img.get("#text") for img in (album_node.get("image") or []) if isinstance(img, dict) and img.get("#text")),
            None,
        ),
    }


def update_now_playing(artist: str, track: str, album: Optional[str] = None) -> None:
    if not artist or not track or not scrobble_ready():
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
    if not artist or not track:
        return
    if not scrobble_ready():
        logger.debug("Scrobble skipped: LASTFM_SESSION_KEY not configured.")
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
        logger.debug("Scrobble failed for %s — %s", artist, track)
    else:
        logger.info("Scrobbled: %s — %s", artist, track)
