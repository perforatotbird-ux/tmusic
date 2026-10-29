"""
T-Music Nested Catalog Rollback Tool

Restores catalog artists and albums from a snapshot taken prior to folder tree synchronization.
Supports rolling back an entire batch or a specific folder, with full dry-run simulation.

Usage:
    python scripts/rollback_nested_catalog.py [--backup-file PATH] [--folder-id ID] [--dry-run] [--list]
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

# Ensure backend is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from app.core.database import SessionLocal
from app.models.music import MusicArtist, MusicAlbum
from sqlalchemy import select


BACKUP_DIR = BASE_DIR / "data" / "catalog_snapshots"


def list_backups() -> List[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob("catalog_snapshot_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def rollback_folder_snapshot(db, snapshot: Dict[str, Any], dry_run: bool = False) -> Dict[str, int]:
    folder_ids = snapshot["folder_ids"]
    snap_artists = {a["folder_id"]: a for a in snapshot["artists"]}
    snap_albums = {alb["folder_id"]: alb for alb in snapshot["albums"]}

    curr_artists = {
        a.folder_id: a for a in db.execute(
            select(MusicArtist).where(MusicArtist.folder_id.in_(folder_ids))
        ).scalars().all()
    }
    curr_albums = {
        alb.folder_id: alb for alb in db.execute(
            select(MusicAlbum).where(MusicAlbum.folder_id.in_(folder_ids))
        ).scalars().all()
    }

    # 1. Delete newly created albums
    deleted_albums = 0
    for fid, alb in list(curr_albums.items()):
        if fid not in snap_albums:
            if not dry_run:
                db.delete(alb)
            deleted_albums += 1
            del curr_albums[fid]

    # 2. Restore modified albums
    updated_albums = 0
    for fid, snap_alb in snap_albums.items():
        if fid in curr_albums:
            curr_alb = curr_albums[fid]
            changed = False
            if curr_alb.artist_id != snap_alb["artist_id"]:
                if not dry_run:
                    curr_alb.artist_id = snap_alb["artist_id"]
                changed = True
            if curr_alb.title != snap_alb["title"]:
                if not dry_run:
                    curr_alb.title = snap_alb["title"]
                changed = True
            if curr_alb.year != snap_alb.get("year"):
                if not dry_run:
                    curr_alb.year = snap_alb.get("year")
                changed = True
            if changed:
                updated_albums += 1

    # 3. Re-create deleted albums
    recreated_albums = 0
    for fid, snap_alb in snap_albums.items():
        if fid not in curr_albums:
            if not dry_run:
                recreated = MusicAlbum(
                    id=snap_alb["id"],
                    folder_id=snap_alb["folder_id"],
                    artist_id=snap_alb["artist_id"],
                    title=snap_alb["title"],
                    year=snap_alb.get("year"),
                    mbid=snap_alb.get("mbid"),
                    cover_url=snap_alb.get("cover_url"),
                    confidence=snap_alb.get("confidence", 0),
                    custom_image_key=snap_alb.get("custom_image_key"),
                    enriched_at=parse_datetime(snap_alb.get("enriched_at")),
                    created_at=parse_datetime(snap_alb.get("created_at")) or datetime.now(timezone.utc),
                    updated_at=parse_datetime(snap_alb.get("updated_at")) or datetime.now(timezone.utc),
                )
                db.add(recreated)
            recreated_albums += 1

    if not dry_run:
        db.flush()

    # 4. Detach parent_id on newly created artists to avoid FK constraint issues during delete
    for fid, art in list(curr_artists.items()):
        if fid not in snap_artists:
            if not dry_run:
                art.parent_id = None

    if not dry_run:
        db.flush()

    # 5. Delete newly created artists
    deleted_artists = 0
    for fid, art in list(curr_artists.items()):
        if fid not in snap_artists:
            if not dry_run:
                db.delete(art)
            deleted_artists += 1
            del curr_artists[fid]

    # 6. Restore modified artists
    updated_artists = 0
    for fid, snap_art in snap_artists.items():
        if fid in curr_artists:
            curr_art = curr_artists[fid]
            changed = False
            if curr_art.kind != snap_art["kind"]:
                if not dry_run:
                    curr_art.kind = snap_art["kind"]
                changed = True
            if curr_art.parent_id != snap_art["parent_id"]:
                if not dry_run:
                    curr_art.parent_id = snap_art["parent_id"]
                changed = True
            if curr_art.display_name != snap_art["display_name"]:
                if not dry_run:
                    curr_art.display_name = snap_art["display_name"]
                changed = True
            if curr_art.search_name != snap_art.get("search_name"):
                if not dry_run:
                    curr_art.search_name = snap_art.get("search_name")
                changed = True
            if changed:
                updated_artists += 1

    # 7. Re-create deleted artists (if any)
    recreated_artists = 0
    for fid, snap_art in snap_artists.items():
        if fid not in curr_artists:
            if not dry_run:
                recreated = MusicArtist(
                    id=snap_art["id"],
                    folder_id=snap_art["folder_id"],
                    kind=snap_art["kind"],
                    parent_id=snap_art["parent_id"],
                    display_name=snap_art["display_name"],
                    search_name=snap_art.get("search_name"),
                    mbid=snap_art.get("mbid"),
                    image_url=snap_art.get("image_url"),
                    bio=snap_art.get("bio"),
                    wikipedia_url=snap_art.get("wikipedia_url"),
                    confidence=snap_art.get("confidence", 0),
                    locked=snap_art.get("locked", False),
                    custom_image_key=snap_art.get("custom_image_key"),
                    enriched_at=parse_datetime(snap_art.get("enriched_at")),
                    created_at=parse_datetime(snap_art.get("created_at")) or datetime.now(timezone.utc),
                    updated_at=parse_datetime(snap_art.get("updated_at")) or datetime.now(timezone.utc),
                )
                db.add(recreated)
            recreated_artists += 1

    return {
        "deleted_artists": deleted_artists,
        "updated_artists": updated_artists,
        "recreated_artists": recreated_artists,
        "deleted_albums": deleted_albums,
        "updated_albums": updated_albums,
        "recreated_albums": recreated_albums,
    }


def main():
    parser = argparse.ArgumentParser(description="Rollback nested catalog changes from a snapshot")
    parser.add_argument("--backup-file", type=str, default=None, help="Path to snapshot JSON file")
    parser.add_argument("--folder-id", type=int, default=None, help="Only rollback specific root folder ID")
    parser.add_argument("--dry-run", action="store_true", help="Simulate rollback without committing")
    parser.add_argument("--list", action="store_true", help="List available catalog backup snapshots")
    args = parser.parse_args()

    if args.list:
        backups = list_backups()
        if not backups:
            print("No catalog snapshots found in data/catalog_snapshots/.")
        else:
            print(f"Available catalog snapshots ({len(backups)}):")
            for b in backups:
                mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                print(f"  {b.name}  ({mtime}, {b.stat().st_size / 1024:.1f} KB)")
        return

    backup_path = None
    if args.backup_file:
        backup_path = Path(args.backup_file)
        if not backup_path.is_absolute():
            backup_path = (BASE_DIR / backup_path).resolve()
    else:
        backups = list_backups()
        if not backups:
            print("Error: No catalog snapshots found in data/catalog_snapshots/.", file=sys.stderr)
            sys.exit(1)
        backup_path = backups[0]
        print(f"Using latest snapshot: {backup_path.name}")

    if not backup_path.exists():
        print(f"Error: Backup file {backup_path} does not exist.", file=sys.stderr)
        sys.exit(1)

    with open(backup_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Format can be single snapshot or a batch containing 'snapshots' list
    snapshots = data.get("snapshots", [data]) if isinstance(data, dict) else data

    if args.folder_id is not None:
        snapshots = [s for s in snapshots if s.get("root_folder_id") == args.folder_id]
        if not snapshots:
            print(f"Error: Folder {args.folder_id} not found in snapshot {backup_path.name}.", file=sys.stderr)
            sys.exit(1)

    print(f"Starting rollback of {len(snapshots)} folder tree(s) from {backup_path.name}...")
    if args.dry_run:
        print("[DRY-RUN MODE] No changes will be committed.")

    db = SessionLocal()
    total_stats = {
        "deleted_artists": 0,
        "updated_artists": 0,
        "recreated_artists": 0,
        "deleted_albums": 0,
        "updated_albums": 0,
        "recreated_albums": 0,
    }

    try:
        for snap in snapshots:
            root_id = snap.get("root_folder_id")
            root_name = snap.get("root_folder_name", str(root_id))
            stats = rollback_folder_snapshot(db, snap, dry_run=args.dry_run)
            print(f"  [{root_id}] {root_name}: -{stats['deleted_artists']} artists, ~{stats['updated_artists']} artists, -{stats['deleted_albums']} albums, ~{stats['updated_albums']} albums, +{stats['recreated_albums']} albums")
            for k, v in stats.items():
                total_stats[k] += v

        if args.dry_run:
            db.rollback()
            print("\n[DRY-RUN] Rollback simulated successfully. Summary:")
        else:
            db.commit()
            print("\nRollback committed successfully. Summary:")

        print(f"  Artists deleted:    {total_stats['deleted_artists']}")
        print(f"  Artists updated:    {total_stats['updated_artists']}")
        print(f"  Artists recreated:  {total_stats['recreated_artists']}")
        print(f"  Albums deleted:     {total_stats['deleted_albums']}")
        print(f"  Albums updated:     {total_stats['updated_albums']}")
        print(f"  Albums recreated:   {total_stats['recreated_albums']}")
    except Exception as e:
        db.rollback()
        print(f"\nError during rollback: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
