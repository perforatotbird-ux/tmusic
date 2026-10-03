"""Warm the local cover disk cache from catalog remote URLs.

Why this exists (2026-10-03): artist/album/search cards serve
``/api/library/cover?key=<cache-key>`` and never fetch remote URLs on
display — display is cache-only. The cache file (``{key}.bin`` in
``cache/music_images``) is written once at enrichment time. Rows enriched
before that change only store the remote URL, so this script backfills the
disk cache without touching the database.

Safe to run repeatedly: ``fetch_and_cache`` checks the disk first, so cached
URLs are skipped without network I/O. Prints counts only, never URLs or
secrets.

    python scripts/warm_cover_cache.py [--limit N] [--dry-run]
"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.music import MusicAlbum, MusicArtist  # noqa: E402
from app.services.cover_cache_service import cover_cache  # noqa: E402

CONCURRENCY = 4


def collect_urls(limit: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    with SessionLocal() as db:
        artist_urls = db.scalars(
            select(MusicArtist.image_url).where(MusicArtist.image_url.isnot(None))
        ).all()
        album_urls = db.scalars(
            select(MusicAlbum.cover_url).where(MusicAlbum.cover_url.isnot(None))
        ).all()
        for u in list(artist_urls) + list(album_urls):
            if not u or u in seen:
                continue
            seen.add(u)
            urls.append(u)
            if limit and len(urls) >= limit:
                break
    return urls


async def warm(urls: list[str]) -> dict:
    sem = asyncio.Semaphore(CONCURRENCY)
    stats = {"total": len(urls), "cached": 0, "downloaded": 0, "failed": 0}

    async def one(url: str) -> None:
        async with sem:
            key = cover_cache.url_to_key(url)
            if cover_cache.get_cached_path(key) is not None:
                stats["cached"] += 1
                return
            try:
                data, _ = await cover_cache.fetch_and_cache(url)
            except Exception:
                data = None
            if data:
                stats["downloaded"] += 1
            else:
                stats["failed"] += 1

    await asyncio.gather(*(one(u) for u in urls))
    await cover_cache.close()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="Warm at most N distinct URLs (0 = all).")
    parser.add_argument("--dry-run", action="store_true", help="Count distinct URLs, download nothing.")
    args = parser.parse_args()

    urls = collect_urls(args.limit)
    print(f"Distinct remote cover URLs in catalog: {len(urls)}")
    if args.dry_run:
        print("Dry run: nothing downloaded.")
        return 0
    stats = asyncio.run(warm(urls))
    print(
        f"Already cached: {stats['cached']}; "
        f"downloaded: {stats['downloaded']}; failed: {stats['failed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
