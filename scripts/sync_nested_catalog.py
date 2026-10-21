"""
T-Music Nested Catalog Sync Tool

Recursively synchronizes nested folder trees into catalog artists and albums.
Containers with subfolders become compilations or artists with nested children.
Ghost/empty album records under containers are cleaned up.

Automatically takes a full backup snapshot before making any changes, enabling
complete and safe rollback via `scripts/rollback_nested_catalog.py`.

Usage:
    # Sync all 30 detected container folders in one batch:
    python scripts/sync_nested_catalog.py --all

    # Sync single folder by ID:
    python scripts/sync_nested_catalog.py --folder-id 12367 --kind compilation

    # Dry-run simulation (no changes committed):
    python scripts/sync_nested_catalog.py --all --dry-run
"""
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple

# Ensure backend is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from app.core.database import SessionLocal
from app.models.vfs import Folder
from app.models.music import MusicArtist, MusicAlbum
from app.services.music_library_service import CatalogAdminService
from sqlalchemy import select, text


BACKUP_DIR = BASE_DIR / "data" / "catalog_snapshots"

# 30 identified container folders in Music
ALL_CONTAINERS: List[Tuple[int, str, str]] = [
    # Категория 1: Мульти-артист сборники и коллекции (100% аналог «Пранки»)
    (12367, "compilation", "Post-Punk Electronic"),
    (1946, "compilation", "KeyGen Music"),
    (316, "compilation", "8Bit"),
    (14071, "compilation", "Харьковский панк рок 2000х"),
    (3331, "compilation", "True Metal Generation - Punk Rock"),
    # Категория 2: Зонтичные контейнеры артистов (сайд-проекты, сплиты, связанные составы)
    (9229, "artist", "Гарик Сукачёв"),
    (12174, "artist", "Ленинград"),
    (14486, "artist", "ХУГО-УГО (дискография 1990-2010)"),
    (13926, "artist", "Трэш-шапито КАЧ"),
    (14072, "artist", "Хуй Забей"),
    (4787, "artist", "Чиж & Co"),
    (12869, "artist", "Коррозия Металла"),
    (8197, "artist", "Бони'Нем"),
    (13967, "artist", "Убийцы (Убийцы Crystal)"),
    (11404, "artist", "Империя снегов"),
    # Категория 3: Структурированные дискографии по типам релизов
    (2012, "artist", "KMFDM"),
    (11148, "artist", "Оргазм Нострадамуса"),
    (5839, "artist", "ZZ Top"),
    (6173, "artist", "Автоматические удовлетворители"),
    (12923, "artist", "Красная Плесень"),
    (10843, "artist", "ДК"),
    (13396, "artist", "Ногу Свело!"),
    (4886, "artist", "ЧайФ"),
    (2266, "artist", "Lindemann"),
    (5787, "artist", "The Casualties"),
    (1817, "artist", "Joy Division"),
    (17148, "artist", "Йорш"),
    (13229, "artist", "Кувалда"),
    (12764, "artist", "Комитет Охраны Тепла"),
    (6066, "artist", "Голос Омерики"),
]


def get_subtree_folder_ids(db, root_folder_id: int) -> List[int]:
    query = text("""
        WITH RECURSIVE folder_tree AS (
            SELECT id FROM folders WHERE id = :root_id
            UNION ALL
            SELECT f.id FROM folders f
            JOIN folder_tree ft ON f.parent_id = ft.id
            WHERE f.is_trash = FALSE
        )
        SELECT id FROM folder_tree;
    """)
    rows = db.execute(query, {"root_id": root_folder_id}).scalars().all()
    return list(rows)


def take_folder_snapshot(db, root_folder_id: int) -> Dict[str, Any]:
    folder_ids = get_subtree_folder_ids(db, root_folder_id)
    root_folder = db.get(Folder, root_folder_id)

    artists = db.execute(
        select(MusicArtist).where(MusicArtist.folder_id.in_(folder_ids))
    ).scalars().all()

    albums = db.execute(
        select(MusicAlbum).where(MusicAlbum.folder_id.in_(folder_ids))
    ).scalars().all()

    artist_data = []
    for a in artists:
        artist_data.append({
            "id": a.id,
            "folder_id": a.folder_id,
            "kind": a.kind,
            "parent_id": a.parent_id,
            "display_name": a.display_name,
            "search_name": a.search_name,
            "mbid": a.mbid,
            "image_url": a.image_url,
            "bio": a.bio,
            "wikipedia_url": a.wikipedia_url,
            "confidence": a.confidence,
            "locked": a.locked,
            "custom_image_key": a.custom_image_key,
            "enriched_at": a.enriched_at.isoformat() if a.enriched_at else None,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None,
        })

    album_data = []
    for alb in albums:
        album_data.append({
            "id": alb.id,
            "folder_id": alb.folder_id,
            "artist_id": alb.artist_id,
            "title": alb.title,
            "year": alb.year,
            "mbid": alb.mbid,
            "cover_url": alb.cover_url,
            "confidence": alb.confidence,
            "custom_image_key": alb.custom_image_key,
            "enriched_at": alb.enriched_at.isoformat() if alb.enriched_at else None,
            "created_at": alb.created_at.isoformat() if alb.created_at else None,
            "updated_at": alb.updated_at.isoformat() if alb.updated_at else None,
        })

    return {
        "root_folder_id": root_folder_id,
        "root_folder_name": root_folder.name if root_folder else str(root_folder_id),
        "folder_ids": folder_ids,
        "artists": artist_data,
        "albums": album_data,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_backup_file(snapshots: List[Dict[str, Any]]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = BACKUP_DIR / f"catalog_snapshot_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"created_at": datetime.now(timezone.utc).isoformat(), "snapshots": snapshots}, f, ensure_ascii=False, indent=2)
    return path


def main():
    parser = argparse.ArgumentParser(description="Recursively sync folder tree into catalog")
    parser.add_argument("--all", action="store_true", help="Sync all 30 detected container folders")
    parser.add_argument("--folder-id", type=int, default=None, help="VFS folder ID to sync")
    parser.add_argument("--name", type=str, default=None, help="VFS folder name to search if folder-id not specified")
    parser.add_argument("--kind", type=str, default=None, help="Catalog kind for root folder (artist or compilation)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate changes without committing")
    parser.add_argument("--no-backup", action="store_true", help="Skip creating backup snapshot before sync")
    args = parser.parse_args()

    tasks: List[Tuple[int, str, str]] = []

    db = SessionLocal()
    try:
        if args.all:
            tasks = list(ALL_CONTAINERS)
        else:
            folder_id = args.folder_id
            if folder_id is None:
                if args.name:
                    f = db.execute(select(Folder).where(Folder.name.ilike(f"%{args.name}%"))).scalars().first()
                    if not f:
                        print(f"Error: Folder matching '{args.name}' not found.", file=sys.stderr)
                        sys.exit(1)
                    folder_id = f.id
                else:
                    folder_id = 15300  # Default to Пранки

            folder = db.get(Folder, folder_id)
            if not folder:
                print(f"Error: Folder {folder_id} not found.", file=sys.stderr)
                sys.exit(1)

            kind = args.kind if args.kind else "compilation"
            tasks = [(folder.id, kind, folder.name)]

        print(f"Targeting {len(tasks)} folder tree(s) for catalog synchronization...")
        if args.dry_run:
            print("[DRY-RUN MODE] Changes will be simulated and rolled back.")

        # Take backup snapshot first
        backup_path = None
        if not args.no_backup:
            print("Taking pre-sync backup snapshot of catalog state...")
            snapshots = []
            for fid, kind, name in tasks:
                snap = take_folder_snapshot(db, fid)
                snapshots.append(snap)
            backup_path = save_backup_file(snapshots)
            print(f"Backup snapshot saved to: {backup_path.relative_to(BASE_DIR)}")

        # Execute sync
        total_stats = {
            "created_artists": 0,
            "updated_artists": 0,
            "created_albums": 0,
            "deleted_empty_albums": 0,
        }

        for fid, kind, name in tasks:
            print(f"  Syncing [{fid}] {name} (kind: {kind})...")
            stats = CatalogAdminService.sync_catalog_folder_tree(
                db, fid, catalog_kind=kind, commit=(not args.dry_run)
            )
            print(f"    +{stats['created_artists']} artists, ~{stats['updated_artists']} artists, +{stats['created_albums']} albums, -{stats['deleted_empty_albums']} empty albums")
            for k in total_stats:
                total_stats[k] += stats[k]

        if args.dry_run:
            db.rollback()
            print("\n[DRY-RUN] Sync simulation complete. Summary of simulated changes:")
        else:
            print("\nSync successfully completed and committed to database! Summary:")

        print(f"  Total created artists:      {total_stats['created_artists']}")
        print(f"  Total updated artists:      {total_stats['updated_artists']}")
        print(f"  Total created albums:       {total_stats['created_albums']}")
        print(f"  Total deleted empty albums: {total_stats['deleted_empty_albums']}")

        if backup_path and not args.dry_run:
            rel_backup = backup_path.relative_to(BASE_DIR)
            print("\n" + "=" * 70)
            print("ROLLBACK INSTRUCTIONS:")
            print(f"To revert all these changes at any time, run:")
            print(f"  python scripts/rollback_nested_catalog.py --backup-file {rel_backup}")
            print("To preview the rollback before applying it:")
            print(f"  python scripts/rollback_nested_catalog.py --backup-file {rel_backup} --dry-run")
            print("=" * 70)

    finally:
        db.close()


if __name__ == "__main__":
    main()
