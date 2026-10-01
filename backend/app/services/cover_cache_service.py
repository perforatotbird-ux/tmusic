"""
T-Music Cover Cache & Proxy Service
Zero-trust secure image downloader and permanent disk cache.
Compatible with CloudByte's SHA-1 32-char cache keys (.bin and .meta).
Defends against SSRF, Path Traversal, and Malicious payloads (SVG/Executables).
"""
import os
import socket
import hashlib
import ipaddress
import urllib.parse
import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict
import aiohttp
from aiohttp.abc import AbstractResolver

from app.core.config import settings

logger = logging.getLogger(__name__)

# Back-compat alias: router imports this name. Source of truth is music.env
# (COVER_MAX_IMAGE_BYTES); do not hardcode a second value here.
MAX_IMAGE_BYTES = settings.COVER_MAX_IMAGE_BYTES
HTTP_TIMEOUT = aiohttp.ClientTimeout(
    total=settings.COVER_HTTP_TIMEOUT_SEC,
    connect=settings.COVER_HTTP_CONNECT_TIMEOUT_SEC,
)
USER_AGENT = "CloudByte-MusicLibrary/1.0 (self-hosted personal music library)"

_MAGICS = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
    (b"BM", "image/bmp"),
)

_EXT_BY_CT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
}


def _detect_mime_and_validate(data: bytes) -> Optional[str]:
    """Validate image format by magic bytes. SVG is rejected to prevent Stored XSS."""
    if not data or len(data) < 12:
        return None
    for magic, mime in _MAGICS:
        if data.startswith(magic):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime
    return None


async def is_safe_host(url: str) -> bool:
    """Non-blocking DNS check preventing SSRF to local networks and cloud metadata."""
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname
        if not host:
            return False

        loop = asyncio.get_running_loop()
        addr_info = await loop.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        for _, _, _, _, sockaddr in addr_info:
            if not _ip_allowed(sockaddr[0], host):
                return False
        return True
    except Exception as e:
        logger.warning("DNS resolution failed for SSRF check (%s): %s", url, e)
        return False


async def _is_public_http_url(url: str) -> bool:
    """Scheme + host + resolved-IP check (SSRF)."""
    return await is_safe_host(url)


def _ip_allowed(ip_str: str, host: str) -> bool:
    """Single-IP SSRF verdict (shared by the pre-check and the pinning resolver)."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip_str == "169.254.169.254"
    ):
        logger.warning("SSRF blocked attempt to connect to restricted IP: %s (host: %s)", ip_str, host)
        return False
    return True


async def _resolve_validated(host: str) -> Optional[list]:
    """Resolve + validate ALL addresses, fail closed if any is restricted.

    Returns [(ip_str, family), ...] or None. Callers must connect ONLY to these
    IPs (see _PinnedResolver) — resolving again at connect time would reopen
    the DNS-rebinding TOCTOU window.
    """
    try:
        loop = asyncio.get_running_loop()
        addr_info = await loop.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except Exception as e:
        logger.warning("DNS resolution failed for SSRF check (host: %s): %s", host, e)
        return None
    entries = []
    for _, _, family, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        if not _ip_allowed(ip_str, host):
            return None
        entries.append((ip_str, family))
    return entries or None


class _PinnedResolver(AbstractResolver):
    """Resolver that answers ONLY with pre-validated IPs.

    aiohttp would otherwise re-resolve the hostname at connect time, letting a
    rebind attack swap in a private IP after our check passed (TOCTOU).
    Certificate/SNI validation still uses the original hostname, so HTTPS
    trust is unaffected — only the destination address is pinned.
    """

    def __init__(self, entries: list):
        self._entries = entries

    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        return [
            {"hostname": host, "host": ip, "port": port,
             "family": fam, "proto": 0, "flags": 0}
            for ip, fam in self._entries
            if family == socket.AF_UNSPEC or fam == family
        ]

    async def close(self):
        pass


async def _fetch_safe(session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
    """
    GET with manual redirect handling. Every hop is resolved once, validated,
    and then fetched through a connector pinned to those exact IPs — the
    connection cannot silently re-resolve to a different (internal) address.
    Returns body bytes or None.
    """
    current = url
    for _ in range(4):  # initial + up to 3 redirects
        parsed = urllib.parse.urlsplit(current)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        entries = await _resolve_validated(parsed.hostname)
        if not entries:
            logger.warning("SSRF blocked fetch to %s", current)
            return None
        connector = aiohttp.TCPConnector(resolver=_PinnedResolver(entries), limit=1)
        try:
            async with aiohttp.ClientSession(connector=connector) as direct_session:
                async with direct_session.get(
                    current,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "image/webp,image/jpeg,image/png,*/*;q=0.8",
                    },
                    allow_redirects=False,
                ) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            return None
                        current = urllib.parse.urljoin(current, location)
                        continue
                    if resp.status != 200:
                        return None
                    data = await resp.read()
                    if len(data) > MAX_IMAGE_BYTES:
                        return None
                    return data
        except Exception as e:
            logger.warning("Failed to fetch image from %s: %s", current, e)
            return None
        finally:
            await connector.close()
    logger.warning("Too many redirects while fetching %s", url)
    return None


class CoverCacheService:
    """Service managing permanent cover caching and image retrieval."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._in_flight: Dict[str, asyncio.Future] = {}
        self._fetch_tasks = set()
        self._closing = False
        self._generation = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def _safe_resolve_in(root: Path, name: str) -> Optional[Path]:
        """Resolve ``name`` inside ``root`` or return None on escape/missing.

        Single choke point for the basename + resolve + is_relative_to +
        exists + is_file chain used by both lookup methods. Security-sensitive:
        keep the containment check here so the two lookups cannot drift apart.
        """
        candidate = (root / os.path.basename(name)).resolve()
        if (
            candidate.is_relative_to(root.resolve())
            and candidate.exists()
            and candidate.is_file()
        ):
            return candidate
        return None

    def _cache_dirs(self, is_custom: bool = False) -> list:
        """Cache roots in lookup order (custom covers first when is_custom)."""
        if is_custom:
            return [settings.COVER_CACHE_DIR, settings.IMG_CACHE_DIR]
        return [settings.IMG_CACHE_DIR, settings.COVER_CACHE_DIR]

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=16)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=HTTP_TIMEOUT,
                headers={"User-Agent": USER_AGENT}
            )
        return self._session

    async def close(self):
        self._closing = True
        self._generation += 1
        tasks = list(self._fetch_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            for future in self._in_flight.values():
                if not future.done():
                    future.set_result((None, None))
            self._in_flight.clear()
        self._fetch_tasks.clear()
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._closing = False

    def url_to_key(self, url: str) -> str:
        """Deterministic 32-char SHA-1 key matching CloudByte canonical cache format."""
        clean = urllib.parse.urlparse(url)
        norm = f"{clean.scheme}://{clean.netloc}{clean.path}"
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:32]

    def get_cached_item(self, key: str, is_custom: bool = False) -> Optional[Tuple[bytes, str]]:
        """
        Locates cached image on disk and returns (bytes, content_type).
        Searches both COVER_CACHE_DIR and IMG_CACHE_DIR, supporting both {key}.bin and {key}.
        """
        safe_name = os.path.basename(key)
        dirs = self._cache_dirs(is_custom)

        for d in dirs:
            # 1. Try direct file (e.g. custom upload "hash.jpg")
            candidate = self._safe_resolve_in(d, safe_name)
            if candidate is not None:
                data = candidate.read_bytes()
                mime = _detect_mime_and_validate(data) or "image/jpeg"
                return data, mime

            # 2. Try .bin file (e.g. CloudByte cache "hash.bin")
            candidate_bin = self._safe_resolve_in(d, f"{safe_name}.bin")
            if candidate_bin is not None:
                data = candidate_bin.read_bytes()
                meta_path = (d / f"{safe_name}.meta").resolve()
                ct = "image/jpeg"
                if meta_path.exists() and meta_path.is_file():
                    try:
                        ct = meta_path.read_text(encoding="utf-8").strip() or ct
                    except Exception:
                        pass
                mime = _detect_mime_and_validate(data) or ct
                return data, mime

        return None

    def get_cached_path(self, key: str, is_custom: bool = False) -> Optional[Path]:
        """Safely locate a cached image file on disk."""
        safe_name = os.path.basename(key)

        for d in self._cache_dirs(is_custom):
            for fname in (safe_name, f"{safe_name}.bin"):
                target = self._safe_resolve_in(d, fname)
                if target is not None:
                    return target
        return None

    async def fetch_and_cache(self, url: str) -> Tuple[Optional[bytes], Optional[str]]:
        """Fetch remote image with in-flight deduplication, SSRF defense, and permanent disk caching."""
        generation = self._generation
        key = self.url_to_key(url)

        # Check local cache first
        cached = self.get_cached_item(key, is_custom=False)
        if cached:
            return cached

        # SSRF validation
        if not await is_safe_host(url):
            return None, None

        # In-flight deduplication
        async with self._lock:
            if self._closing or generation != self._generation:
                return None, None
            if key in self._in_flight:
                future = self._in_flight[key]
            else:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._in_flight[key] = future
                task = asyncio.create_task(self._do_fetch(url, key, future))
                self._fetch_tasks.add(task)
                task.add_done_callback(self._fetch_tasks.discard)

        return await asyncio.shield(future)

    async def _do_fetch(self, url: str, key: str, future: asyncio.Future):
        result = (None, None)
        try:
            session = await self.get_session()
            data = await _fetch_safe(session, url)
            mime = _detect_mime_and_validate(data) if data else None
            if mime:
                bin_dest = settings.IMG_CACHE_DIR / f"{key}.bin"
                meta_dest = settings.IMG_CACHE_DIR / f"{key}.meta"
                bin_dest.write_bytes(data)
                meta_dest.write_text(mime, encoding="utf-8")
                result = (data, mime)
        except Exception as e:
            logger.warning("Failed to fetch image from %s: %s", url, e)
        finally:
            if not future.done():
                future.set_result(result)
            async with self._lock:
                if self._in_flight.get(key) is future:
                    self._in_flight.pop(key, None)

    def save_custom_cover(self, data: bytes) -> Optional[str]:
        """Save a user-uploaded custom cover permanently."""
        if len(data) > MAX_IMAGE_BYTES:
            return None
        mime = _detect_mime_and_validate(data)
        if not mime:
            return None

        ext = _EXT_BY_CT.get(mime, ".jpg")
        key = hashlib.sha1(data).hexdigest()[:32] + ext
        dest = settings.COVER_CACHE_DIR / key
        dest.write_bytes(data)
        return key


cover_cache = CoverCacheService()
