"""
T-Music RAM Cache Service
Maintains in-memory buffers of actively playing tracks to enable zero-latency Range seeks.

Hardening (2026-09 security audit): eviction now enforces BOTH an item-count
cap and a total-bytes cap (RAM_CACHE_MAX_BYTES). A count-only cap let a few
very long tracks exhaust server memory (DoS via memory pressure).
"""
import asyncio
from collections import OrderedDict
from typing import Optional, Dict

from app.core.config import settings


class RamCacheService:
    """LRU in-memory buffer for active audio tracks with count + byte caps."""

    def __init__(self, max_items: int = 15, max_bytes: int = 512 * 1024 * 1024):
        self.max_items = max_items
        self.max_bytes = max_bytes
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._current_bytes: int = 0
        self._locks: Dict[int, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    def get_lock(self, file_id: int) -> asyncio.Lock:
        if file_id not in self._locks:
            self._locks[file_id] = asyncio.Lock()
        return self._locks[file_id]

    async def get(self, file_id: int) -> Optional[bytes]:
        async with self._global_lock:
            if file_id in self._cache:
                self._cache.move_to_end(file_id)
                return self._cache[file_id]
            return None

    async def put(self, file_id: int, data: bytes):
        async with self._global_lock:
            # Single tracks larger than the whole budget are never cached
            if len(data) > self.max_bytes:
                return

            if file_id in self._cache:
                self._current_bytes -= len(self._cache[file_id])
                self._cache.move_to_end(file_id)
                self._cache[file_id] = data
                self._current_bytes += len(data)
            else:
                self._cache[file_id] = data
                self._current_bytes += len(data)

            # Evict least-recently-used entries until both caps are satisfied
            while len(self._cache) > self.max_items or self._current_bytes > self.max_bytes:
                _, evicted = self._cache.popitem(last=False)
                self._current_bytes -= len(evicted)


ram_cache = RamCacheService(
    max_items=settings.RAM_CACHE_MAX_ITEMS,
    max_bytes=settings.RAM_CACHE_MAX_BYTES,
)
