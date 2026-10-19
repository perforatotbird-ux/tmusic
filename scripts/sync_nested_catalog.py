"""
T-Music Nested Catalog Sync Tool

Synchronizes an arbitrary nested folder tree into catalog artists and albums.
Containers with subfolders become compilations (MusicArtist with kind='compilation').
Leaf folders with audio become artists (MusicArtist with kind='artist') and albums.
Ghost/empty album records under containers are cleaned up.

Usage:
    python scripts/sync_nested_catalog.py [--folder-id 15300] [--name "Пранки"] [--kind compilation] [--dry-run]
"""
import sys
import argparse
from pathlib import Path

# Ensure backend is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))

from app.core.database import SessionLocal
from app.models.vfs import Folder
from app.services.music_library_service import CatalogAdminService
from sqlalchemy import select


def main():
    parser = argparse.ArgumentParser(description="Recursively sync folder tree into catalog")
    parser.add_argument("--folder-id", type=int, default=None, help="VFS folder ID to sync")
    parser.add_argument("--name", type=str, default=None, help="VFS folder name to search if folder-id not specified")
    parser.add_argument("--kind", type=str, default="compilation", help="Catalog kind for root folder (default: compilation)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate changes without committing")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        folder_id = args.folder_id
        if folder_id is None:
            if args.name:
                f = db.execute(select(Folder).where(Folder.name.ilike(f"%{args.name}%"))).scalars().first()
                if not f:
                    print(f"Error: Folder with name matching '{args.name}' not found.", file=sys.stderr)
                    sys.exit(1)
                folder_id = f.id
            else:
                # Default to Пранки (15300)
                folder_id = 15300

        folder = db.get(Folder, folder_id)
        if not folder:
            print(f"Error: Folder {folder_id} not found.", file=sys.stderr)
            sys.exit(1)

        print(f"Starting catalog sync for folder: [{folder.id}] {folder.name} (root kind: {args.kind})...")
        if args.dry_run:
            print("[DRY-RUN MODE] Changes will be rolled back.")

        stats = CatalogAdminService.sync_catalog_folder_tree(db, folder.id, catalog_kind=args.kind)

        if args.dry_run:
            db.rollback()
            print("[DRY-RUN] Sync completed (rolled back):")
        else:
            print("Sync successfully committed to database:")

        print(f"  Created artists:        {stats['created_artists']}")
        print(f"  Updated artists:        {stats['updated_artists']}")
        print(f"  Created albums:         {stats['created_albums']}")
        print(f"  Deleted empty albums:   {stats['deleted_empty_albums']}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
