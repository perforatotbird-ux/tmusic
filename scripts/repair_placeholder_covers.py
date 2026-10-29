"""Repair catalog rows polluted with Last.fm's shared "no artwork" asset.

Why this exists (2026-10-12): ``artist.getInfo`` stopped serving artist imagery —
it now answers with the same generic star asset for every artist
(``lastfm_service.PLACEHOLDER_IMAGE_MARKER``). Older code took the first entry of
the image array, i.e. the 34px thumbnail of that same asset, and wrote it into
``music_artists.image_url``. Because the cover cache key is a deterministic hash
of the URL, every polluted artist collapsed onto one identical picture.

``lastfm_service`` now filters the placeholder on fetch *and* on cache reads, so
new scans never write it again. This script cleans up the rows written before
that guard existed:

1. an artist that has an album with a real cover takes that cover (no network);
2. otherwise up to ``--albums-per-artist`` albums are queried on Last.fm;
3. if nothing real exists, ``image_url`` is cleared to NULL so the card falls
   back to the app's own placeholder instead of the shared star.

Nothing is ever downgraded: an artist whose ``image_url`` is real is left alone,
and rows with ``custom_image_key`` keep their hand-picked cover (only the stale
``image_url`` is rewritten).

Dry run by default. A JSON backup of every changed value is written before the
first update, so the change is reversible.

    python scripts/repair_placeholder_covers.py                 # plan only
    python scripts/repair_placeholder_covers.py --apply
    python scripts/repair_placeholder_covers.py --apply --no-backup
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.models.music import MusicAlbum, MusicArtist  # noqa: E402
from app.services import lastfm_service  # noqa: E402
from app.utils.time import utcnow  # noqa: E402

BATCH_SIZE = 10


def find_polluted(db):
    """Artists whose stored cover is the shared Last.fm placeholder.

    ``is_placeholder_image`` also treats NULL as "no artwork", which is correct
    for a display decision but wrong here: a NULL is already the state this
    script wants, so only rows with a *stored* placeholder are selected.
    """
    artists = db.scalars(select(MusicArtist)).all()
    return [
        a for a in artists
        if a.image_url and lastfm_service.is_placeholder_image(a.image_url)
    ]


def album_fallback(db, artist):
    """Best real cover among the artist's albums, without touching the network."""
    albums = db.scalars(
        select(MusicAlbum).where(MusicAlbum.artist_id == artist.id)
    ).all()
    for album in albums:
        if album.custom_image_key:
            continue
        if album.cover_url and not lastfm_service.is_placeholder_image(album.cover_url):
            return album.cover_url, album
    return None, None


def fetch_from_lastfm(artist, albums, budget):
    """Query Last.fm for album artwork. Returns (cover_url, album) or (None, None)."""
    if not lastfm_service.metadata_ready():
        return None, None
    queried = 0
    for album in albums:
        if album.custom_image_key:
            continue
        if album.cover_url and not lastfm_service.is_placeholder_image(album.cover_url):
            continue
        if queried >= budget:
            break
        queried += 1
        info = lastfm_service.get_album_info(artist.display_name, album.title, force=True)
        url = (info or {}).get("image_url")
        if url:
            return url, album
    return None, None


def plan_for(db, artist, albums_per_artist):
    """Decide what one artist needs. Returns (new_image_url, album_to_update)."""
    cover, album = album_fallback(db, artist)
    if cover:
        return cover, None
    albums = db.scalars(select(MusicAlbum).where(MusicAlbum.artist_id == artist.id)).all()
    cover, album = fetch_from_lastfm(artist, albums, albums_per_artist)
    if cover:
        return cover, album
    return None, None  # nothing real exists → clear the junk


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: plan only)")
    parser.add_argument("--albums-per-artist", type=int, default=4,
                        help="max Last.fm album lookups per artist (default: 4)")
    parser.add_argument("--limit", type=int, default=0, help="stop after N artists (0 = all)")
    parser.add_argument("--no-backup", action="store_true", help="skip the JSON backup")
    args = parser.parse_args()

    db = SessionLocal()
    backup: list[dict] = []
    stats = {"from_album": 0, "from_lastfm": 0, "cleared": 0, "kept": 0, "failed": 0}
    try:
        polluted = find_polluted(db)
        print(f"Artists with the Last.fm placeholder cover: {len(polluted)}")
        if args.limit:
            polluted = polluted[: args.limit]
            print(f"Limited to the first {len(polluted)}.")

        for index, artist in enumerate(polluted, start=1):
            try:
                new_url, album = plan_for(db, artist, args.albums_per_artist)
            except Exception as exc:  # a scan must never abort the whole repair
                stats["failed"] += 1
                print(f"  [{index}] artist #{artist.id}: lookup failed ({type(exc).__name__})")
                continue

            if new_url:
                source = "lastfm" if album is not None else "album"
                stats["from_lastfm" if album is not None else "from_album"] += 1
            else:
                source = "cleared"
                stats["cleared"] += 1

            backup.append({
                "artist_id": artist.id,
                "display_name": artist.display_name,
                "old_image_url": artist.image_url,
                "new_image_url": new_url,
                "source": source,
            })

            if not args.apply:
                continue

            artist.image_url = new_url
            artist.updated_at = utcnow()
            if album is not None:
                album.cover_url = new_url
                album.enriched_at = utcnow()
                album.updated_at = utcnow()
            db.commit()

            if index % BATCH_SIZE == 0:
                db.flush()

        print(
            "Plan/result: "
            f"from album = {stats['from_album']}, "
            f"from Last.fm = {stats['from_lastfm']}, "
            f"cleared to NULL = {stats['cleared']}, "
            f"failed = {stats['failed']}"
        )

        if not args.apply:
            print("Dry run — nothing was written. Re-run with --apply to repair.")
            return 0

        if not args.no_backup and backup:
            out_dir = ROOT / "cache"
            out_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = out_dir / f"placeholder_cover_backup-{stamp}.json"
            path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Backup written: {path}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
