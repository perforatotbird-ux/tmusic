"""
T-Music shared HTTP helpers.

Single source of truth for immutable cover-cache headers (md5 ETag +
long-lived Cache-Control). Keeps backend/app/api/library_router.py free
of copy-pasted header dicts.
"""
import hashlib

COVER_CACHE_CONTROL = "public, max-age=604800, immutable"


def cover_etag(value: str) -> str:
    """Quoted md5 ETag for a cover cache key or remote URL."""
    return f'"{hashlib.md5(value.encode("utf-8")).hexdigest()}"'


def cover_cache_headers(value: str) -> dict:
    """Immutable-cache headers for a cover cache key or remote URL."""
    return {
        "Cache-Control": COVER_CACHE_CONTROL,
        "ETag": cover_etag(value),
    }
