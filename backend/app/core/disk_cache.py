"""
T-Music External Provider Disk Cache
Persistent JSON cache for slow, externally-served answers (LRCLIB lyrics,
Last.fm metadata) that change on the scale of months, not requests.

Design notes
------------
- Namespaced (`lyrics`, `lastfm`) so one provider's flush never touches the
  other's entries, and so cache files stay human-inspectable.
- Negative entries are cached too, with a short TTL. Without them, every tap
  on a track with no lyrics hits LRCLIB twice (get + search) forever, and an
  upstream outage becomes a permanent cache-miss storm.
- Bounded per namespace by entry count AND total bytes; eviction is by file
  mtime (accessed files get their mtime refreshed on hit), which approximates
  LRU at zero in-memory cost — the process may restart or run multiple
  workers and the caps still hold.
- Small JSON blobs only. This is NOT an audio/image cache; those have their
  own services (ram_cache_service, cover_cache_service).
"""
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger("tmusic.disk_cache")

# Per-namespace budgets. Lyrics payloads are full song texts (~5-40 KB);
# Last.fm responses are compact summaries.
_NS_BUDGET = {
    "lyrics": {"max_items": 5000, "max_bytes": 20 * 1024 * 1024},
    "lastfm": {"max_items": 5000, "max_bytes": 5 * 1024 * 1024},
}
_DEFAULT_BUDGET = {"max_items": 2000, "max_bytes": 5 * 1024 * 1024}

# How long a "we looked and found nothing" marker stays authoritative.
NEGATIVE_TTL_SEC = 15 * 60


class ExternalDiskCache:
    """Filesystem JSON cache with TTL, negative entries and byte/count caps."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _ns_dir(self, namespace: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in namespace)
        return self.root / safe

    @staticmethod
    def _key(namespace: str, parts: Dict[str, Any]) -> str:
        canon = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(f"{namespace}|{canon}".encode("utf-8")).hexdigest()[:32]

    def _path(self, namespace: str, parts: Dict[str, Any]) -> Path:
        return self._ns_dir(namespace) / f"{self._key(namespace, parts)}.json"

    def get(self, namespace: str, parts: Dict[str, Any]) -> Tuple[bool, Optional[dict]]:
        """Return (hit, value). hit=True with value=None means a cached miss."""
        path = self._path(namespace, parts)
        try:
            if not path.is_file():
                return False, None
            with path.open("r", encoding="utf-8") as fh:
                entry = json.load(fh)
            if not isinstance(entry, dict) or entry.get("expires_at", 0) < time.time():
                return False, None
            try:
                # Touch mtime so eviction-by-age tracks real access patterns.
                now = time.time()
                os.utime(path, (now, now))
            except OSError:
                pass
            return True, entry.get("value")
        except (OSError, ValueError) as e:
            logger.debug("disk cache read failed (%s): %s", path.name, e)
            return False, None

    def put(
        self,
        namespace: str,
        parts: Dict[str, Any],
        value: Optional[dict],
        ttl_sec: int,
    ) -> None:
        """Store value; value=None caches a negative (not-found) result."""
        path = self._path(namespace, parts)
        ttl = NEGATIVE_TTL_SEC if value is None else max(1, int(ttl_sec))
        entry = {"value": value, "expires_at": time.time() + ttl}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(entry, fh, ensure_ascii=False)
            os.replace(tmp, path)
            self._enforce_budget(namespace)
        except OSError as e:
            logger.debug("disk cache write failed (%s): %s", path.name, e)

    def _enforce_budget(self, namespace: str) -> None:
        budget = _NS_BUDGET.get(namespace, _DEFAULT_BUDGET)
        directory = self._ns_dir(namespace)
        try:
            files = [p for p in directory.glob("*.json") if p.is_file()]
        except OSError:
            return
        total = 0
        sized = []
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            total += st.st_size
            sized.append((st.st_mtime, st.st_size, p))
        if len(sized) <= budget["max_items"] and total <= budget["max_bytes"]:
            return
        # Oldest-accessed first: evict until both caps are satisfied.
        sized.sort(key=lambda item: item[0])
        while sized and (len(sized) > budget["max_items"] or total > budget["max_bytes"]):
            mtime, size, p = sized.pop(0)
            try:
                p.unlink()
                total -= size
            except OSError:
                break

    def clear(self, namespace: Optional[str] = None) -> int:
        """Delete cache entries; returns removed file count."""
        namespaces = [namespace] if namespace else list(_NS_BUDGET.keys())
        removed = 0
        for ns in namespaces:
            directory = self._ns_dir(ns)
            if not directory.is_dir():
                continue
            for p in directory.glob("*.json"):
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def stats(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for ns, budget in _NS_BUDGET.items():
            directory = self._ns_dir(ns)
            items = 0
            total = 0
            if directory.is_dir():
                for p in directory.glob("*.json"):
                    try:
                        total += p.stat().st_size
                        items += 1
                    except OSError:
                        pass
            out[ns] = {"items": items, "bytes": total, **budget}
        return out


external_cache = ExternalDiskCache(settings.EXTERNAL_CACHE_DIR)
