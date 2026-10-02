"""
T-Music Lyrics service — LRCLIB proxy (no API key).

Docs: https://lrclib.net/docs
  GET /api/get?artist_name=&track_name=&album_name=&duration=
  GET /api/search?q=

Network failures and timeouts are swallowed (return None). Never logs secrets
(LRCLIB needs none).
"""
import json
import logging
import math
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import settings
from app.core.disk_cache import external_cache
from app.services.lastfm_service import parse_track_meta

logger = logging.getLogger("tmusic.lyrics")

API_BASE = "https://lrclib.net/api"
USER_AGENT = "T-Music/1.0 (lyrics client)"


def lyrics_enabled() -> bool:
    return bool(getattr(settings, "LYRICS_ENABLED", True))


def _get(path: str, params: Dict[str, Any], timeout: float) -> Optional[dict]:
    if not lyrics_enabled():
        return None
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    url = f"{API_BASE}/{path}?{urlencode(clean)}"
    req = Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else None
    except Exception as e:
        # 404 (no match) and network errors both land here as URLError/HTTPError.
        logger.debug("LRCLIB %s failed: %s", path, e)
        return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _duration(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _normalize(node: Dict[str, Any]) -> Optional[dict]:
    if not isinstance(node, dict):
        return None
    plain = _text(node.get("plainLyrics"))
    synced = _text(node.get("syncedLyrics"))
    if not plain and not synced:
        return None
    dur_sec = _duration(node.get("duration"))
    return {
        "found": True,
        "source": "lrclib",
        "plain": plain or None,
        "synced": synced or None,
        "artist": _text(node.get("artistName")) or None,
        "track": _text(node.get("trackName")) or None,
        "album": _text(node.get("albumName")) or None,
        "duration_sec": dur_sec,
    }


def _search_and_pick(
    artist: str,
    track: str,
    duration: Optional[int],
    timeout: float,
) -> Optional[dict]:
    """LRCLIB /api/search, best hit wins: synced first, exact artist, then
    shortest duration gap."""
    query = f"{artist} {track}".strip() if artist and artist != "Unknown" else track
    hits = _get("search", {"q": query}, timeout)
    if not isinstance(hits, list) or not hits:
        return None

    def score(node: dict) -> tuple:
        has_sync = 0 if _text(node.get("syncedLyrics")) else 1
        artist_match = 0
        if artist and artist != "Unknown":
            artist_match = 0 if _text(node.get("artistName")).lower() == artist.lower() else 1
        dur = _duration(node.get("duration"))
        dur_gap = 9999.0
        if duration is not None and dur is not None:
            try:
                dur_gap = abs(float(dur) - float(duration))
            except (ValueError, TypeError):
                dur_gap = 9999.0
        return (has_sync, artist_match, dur_gap)

    for node in sorted((node for node in hits if isinstance(node, dict)), key=score):
        normalized = _normalize(node)
        if normalized:
            return normalized
    return None


def _lookup_upstream(
    artist: str,
    track: str,
    album: Optional[str],
    parsed_album: Optional[str],
    duration: Optional[int],
    timeout: float,
) -> Optional[dict]:
    """Staged lookup against LRCLIB. No caching here.

    LRCLIB's /api/get matches EXACTLY on every parameter supplied, so a single
    request carrying a wrong album or a slightly-off title 404s even when the
    song exists. Each step therefore drops a constraint rather than loosening
    all of them at once:

      1. artist + title + album  (exact, cheapest to rule out)
      2. artist + title           (album was wrong or absent upstream)
      3. /api/search on "artist title"  (title spelled differently)
      4. /api/search on title alone     (artist unknown or unverifiable)
    """
    if not track:
        return None
    known_artist = bool(artist) and artist != "Unknown"
    album_hint = album or parsed_album

    if known_artist:
        attempts = (
            {"artist_name": artist, "track_name": track,
             "album_name": album_hint, "duration": duration},
            {"artist_name": artist, "track_name": track, "duration": duration},
        )
        for params in attempts:
            data = _get("get", params, timeout)
            if data:
                normalized = _normalize(data)
                if normalized:
                    return normalized

    found = _search_and_pick(artist, track, duration, timeout)
    if found:
        return found

    # Last resort: the title alone, for tracks whose filename gave no artist.
    if known_artist and artist:
        return _search_and_pick("", track, duration, timeout)
    return None


def get_lyrics(
    artist: Optional[str] = None,
    track: Optional[str] = None,
    album: Optional[str] = None,
    duration_sec: Optional[float] = None,
    file_name: Optional[str] = None,
) -> Optional[dict]:
    """Cached best-effort lyrics lookup.

    The disk cache keys on the RESOLVED (post parse_track_meta) identity of
    the request, so "Artist - Track.mp3" and an explicit artist+track lookup
    for the same song share one entry. A `None` result is also cached (short
    TTL) so tracks without lyrics stop re-probing LRCLIB on every open.
    """
    if not lyrics_enabled():
        return None
    parsed_artist, parsed_track, parsed_album = parse_track_meta(
        file_name, artist, track, album
    )
    artist = (artist or parsed_artist or "").strip()
    track = (track or parsed_track or "").strip()
    if not track or artist == "Unknown" and not track:
        return None
    if not track:
        return None

    timeout = float(getattr(settings, "LYRICS_HTTP_TIMEOUT_SEC", 8) or 8)
    safe_duration = _duration(duration_sec)
    duration = int(safe_duration) if safe_duration and safe_duration > 0 else None

    cache_parts = {
        "artist": artist,
        "track": track,
        "album": (album or "").strip(),
        "duration": duration,
    }
    hit, cached = external_cache.get("lyrics", cache_parts)
    if hit:
        return cached

    result = _lookup_upstream(
        artist=artist,
        track=track,
        album=album,
        parsed_album=parsed_album,
        duration=duration,
        timeout=timeout,
    )
    external_cache.put(
        "lyrics",
        cache_parts,
        result,
        ttl_sec=int(getattr(settings, "LYRICS_CACHE_TTL_SEC", 90 * 24 * 3600)),
    )
    return result
