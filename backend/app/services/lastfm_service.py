"""
T-Music Last.fm integration (metadata + optional scrobbling).

music.env:
  LASTFM_API_KEY
  LASTFM_SHARED_SECRET (or LASTFM_API_SECRET) — metadata + signed write calls
  LASTFM_SESSION_KEY — required only for scrobble / now-playing
  LASTFM_USERNAME — optional

Read methods (artist.getInfo, track.getInfo) need api_key only.
Write methods need api_key + secret + session key.

Never logs secrets. Write calls return explicit delivery outcomes.
"""
import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import settings
from app.core.disk_cache import external_cache

logger = logging.getLogger("tmusic.scrobble")

API_URL = "https://ws.audioscrobbler.com/2.0/"

# Last.fm removed artist imagery from the API: `artist.getInfo` now answers with
# the same generic "star" asset for every artist and every size. Writing it into
# a row is worse than writing nothing — the URL is identical for all artists, so
# the deterministic cover_cache key collapses every scanned artist onto one
# placeholder image, and it silently replaces artwork that other imports had
# already supplied. Album artwork is still real, so albums stay a valid source.
PLACEHOLDER_IMAGE_MARKER = "2a96cbd8b46e442fc41c2b86b821562f"

# artist.getInfo returns the image array smallest → largest. The first entry is
# the 34px thumbnail, which is what the old `next(...)` picked.
_IMAGE_SIZE_RANK = {"mega": 5, "extralarge": 4, "large": 3, "medium": 2, "small": 1, "": 0}

# Last.fm ends every wiki summary with this link text. When the artist has no
# wiki at all the summary is *only* this stub — writing it as a bio is noise.
BIO_STUB = "Read more on Last.fm"


def is_placeholder_image(url: Optional[str]) -> bool:
    """True for Last.fm's generic 'no artwork' asset, and for empty values."""
    if not url:
        return True
    return PLACEHOLDER_IMAGE_MARKER in url.lower()


def pick_image(images: Any) -> Optional[str]:
    """Largest non-placeholder URL from a Last.fm image array (or None)."""
    best_rank, best_url = -1, None
    for img in images or []:
        if not isinstance(img, dict):
            continue
        url = img.get("#text")
        if not url or is_placeholder_image(url):
            continue
        rank = _IMAGE_SIZE_RANK.get(str(img.get("size") or "").lower(), 0)
        if rank > best_rank:
            best_rank, best_url = rank, url
    return best_url


def clean_bio(summary: Optional[str]) -> str:
    """Strip Last.fm's trailing 'Read more on Last.fm' link text.

    Returns "" when nothing but the stub remains, so callers can treat it as
    "no bio" instead of storing the placeholder as the artist's biography.
    """
    text = (summary or "").strip()
    if text.endswith(BIO_STUB):
        text = text[: -len(BIO_STUB)].strip()
    return text


def _sanitize_cached(info: Optional[dict]) -> Optional[dict]:
    """Re-apply the provider guards to a value read from the disk cache.

    The Last.fm cache lives for 14 days and survives restarts, so entries
    written before the placeholder filter existed are still on disk. Filtering
    on read is what keeps a stale entry from re-injecting the shared artwork or
    the «Read more on Last.fm» stub into the catalog.

    Only keys that are actually present are touched: this is a filter, not a
    normaliser, and adding an `image_url` that was never cached would make a
    cache hit differ from the fetch that produced it.
    """
    if not isinstance(info, dict):
        return info
    clean = dict(info)
    if "image_url" in clean and is_placeholder_image(clean.get("image_url")):
        clean["image_url"] = None
    if "summary" in clean:
        clean["summary"] = clean_bio(clean.get("summary"))
    return clean


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
    except HTTPError as e:
        # A miss is a normal provider answer, not an outage: Last.fm reports
        # "Album not found" / "Track not found" as an HTTP 404 with a JSON
        # error body. Scans ask about many albums, so logging those as
        # failures would bury real problems.
        try:
            body = e.read().decode("utf-8", errors="replace")
            expected = isinstance(json.loads(body).get("error"), int)
        except Exception:
            expected = False
        if expected:
            logger.debug("Last.fm %s: %s", params.get("method"), body[:120])
        else:
            logger.warning("Last.fm request failed (HTTP %s) for %s", e.code, params.get("method"))
        return None
    except (URLError, OSError, ValueError) as e:
        logger.warning("Last.fm request failed (%s)", type(e).__name__)
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


def get_artist_info(artist: str, force: bool = False) -> Optional[dict]:
    """artist.getInfo via the external disk cache.

    `force=True` skips the cache and rewrites it — the admin «Сканировать»
    action must reflect Last.fm right now, not a snapshot up to 14 days old.
    """
    if not artist or not metadata_ready():
        return None
    parts = {"m": "artist", "artist": artist.strip().casefold()}
    if not force:
        hit, cached = external_cache.get("lastfm", parts)
        if hit:
            return _sanitize_cached(cached)
    result = _fetch_artist_info(artist)
    external_cache.put(
        "lastfm", parts, result, ttl_sec=int(getattr(settings, "LASTFM_CACHE_TTL_SEC", 14 * 24 * 3600))
    )
    return result


def _fetch_artist_info(artist: str) -> Optional[dict]:
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
        "summary": clean_bio(summary),
        "similar": similar[:8],
        "tags": tags[:10],
        # None whenever Last.fm only has the shared placeholder — see
        # PLACEHOLDER_IMAGE_MARKER. Callers must not fall back to it.
        "image_url": pick_image(node.get("image")),
    }


def get_track_info(artist: str, track: str, album: Optional[str] = None, force: bool = False) -> Optional[dict]:
    """track.getInfo via the external disk cache (misses cached briefly too)."""
    if not artist or not track or not metadata_ready():
        return None
    parts = {
        "m": "track",
        "artist": artist.strip().casefold(),
        "track": track.strip().casefold(),
        "album": (album or "").strip().casefold(),
    }
    if not force:
        hit, cached = external_cache.get("lastfm", parts)
        if hit:
            return _sanitize_cached(cached)
    result = _fetch_track_info(artist, track, album)
    external_cache.put(
        "lastfm", parts, result, ttl_sec=int(getattr(settings, "LASTFM_CACHE_TTL_SEC", 14 * 24 * 3600))
    )
    return result


def _fetch_track_info(artist: str, track: str, album: Optional[str] = None) -> Optional[dict]:
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
        "image_url": pick_image(album_node.get("image")),
    }


def get_album_info(artist: str, album: str, force: bool = False) -> Optional[dict]:
    """album.getInfo via the external disk cache.

    Unlike artist artwork, Last.fm album covers are still real — which makes
    this the working cover source for an artist that has none of its own.
    """
    if not artist or not album or not metadata_ready():
        return None
    parts = {
        "m": "album",
        "artist": artist.strip().casefold(),
        "album": album.strip().casefold(),
    }
    if not force:
        hit, cached = external_cache.get("lastfm", parts)
        if hit:
            return _sanitize_cached(cached)
    result = _fetch_album_info(artist, album)
    external_cache.put(
        "lastfm", parts, result, ttl_sec=int(getattr(settings, "LASTFM_CACHE_TTL_SEC", 14 * 24 * 3600))
    )
    return result


def _fetch_album_info(artist: str, album: str) -> Optional[dict]:
    """album.getInfo — release artwork, mbid, wiki summary."""
    if not artist or not album or not metadata_ready():
        return None
    data = _request(
        {"method": "album.getInfo", "artist": artist, "album": album},
        signed=False,
        method="GET",
    )
    if not data:
        return None
    # A miss comes back as {"error": 6, "message": "Album not found"} — no node.
    node = data.get("album")
    if not isinstance(node, dict) or not node:
        return None
    return {
        "name": node.get("name") or album,
        "artist": node.get("artist") or artist,
        "mbid": node.get("mbid") or None,
        "url": node.get("url") or None,
        "summary": clean_bio((node.get("wiki") or {}).get("summary")),
        "image_url": pick_image(node.get("image")),
    }


def _write_outcome(result, node_name: str) -> dict:
    if not isinstance(result, dict):
        return {"ok": False, "code": "provider_unavailable"}
    if result.get("error") is not None:
        return {"ok": False, "code": "provider_rejected"}
    node = result.get(node_name)
    if not isinstance(node, dict):
        return {"ok": False, "code": "invalid_response"}
    entries = node.get("scrobble", []) if node_name == "scrobbles" else [node]
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list) or not entries or not all(isinstance(x, dict) for x in entries):
        return {"ok": False, "code": "invalid_response"}
    for entry in entries:
        ignored = entry.get("ignoredMessage")
        if not isinstance(ignored, dict) or str(ignored.get("code")) != "0":
            return {"ok": False, "code": "ignored"}
    if node_name == "scrobbles":
        attrs = node.get("@attr") or {}
        if not isinstance(attrs, dict):
            return {"ok": False, "code": "invalid_response"}
        try:
            if int(attrs.get("accepted", 0)) < 1 or int(attrs.get("ignored", 0)) != 0:
                return {"ok": False, "code": "ignored"}
        except (TypeError, ValueError, OverflowError):
            return {"ok": False, "code": "invalid_response"}
    return {"ok": True, "code": "accepted"}


def update_now_playing(artist: str, track: str, album: Optional[str] = None) -> dict:
    if not artist or not track or not scrobble_ready():
        return {"ok": False, "code": "skipped"}
    params = {"method": "track.updateNowPlaying", "artist": artist, "track": track}
    if album:
        params["album"] = album
    outcome = _write_outcome(_post(params), "nowplaying")
    if not outcome["ok"]:
        logger.warning("Last.fm now-playing delivery failed: %s", outcome["code"])
    return outcome


def scrobble(artist: str, track: str, album: Optional[str] = None,
             timestamp: Optional[int] = None) -> dict:
    if not artist or not track or not scrobble_ready():
        return {"ok": False, "code": "skipped"}
    params = {"method": "track.scrobble", "artist": artist, "track": track,
              "timestamp": int(timestamp or time.time())}
    if album:
        params["album"] = album
    outcome = _write_outcome(_post(params), "scrobbles")
    if outcome["ok"]:
        logger.info("Scrobbled: %s — %s", artist, track)
    else:
        logger.warning("Scrobble delivery failed: %s", outcome["code"])
    return outcome
