"""
T-Music Catalog Kind Renormalization

The first version of `sync_catalog_folder_tree` derived a card's kind from the
folder *shape*: «has subfolders» → `compilation`, «leaf with audio» → `artist`.
In a discography tree (Artist → «01.Albums» → album → «Covers») that made every
album folder an `artist` card, so the admin list showed albums as «Артист».

`CatalogAdminService.classify_node_kind` now decides by content:
a node that holds audio itself is a release (`standalone_album`), anything else
is a grouping folder (`genre_collection`). This script applies that rule to the
cards already in the database.

Before writing anything it stores a snapshot in `data/catalog_snapshots/` that
`scripts/rollback_nested_catalog.py` can restore (`kind`/`parent_id`/albums).

Usage:
    # Show what would change (default, nothing is written):
    python scripts/reclassify_catalog_kinds.py

    # Apply the reclassification:
    python scripts/reclassify_catalog_kinds.py --apply

    # Apply without taking a snapshot (not recommended):
    python scripts/reclassify_catalog_kinds.py --apply --no-backup
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from sqlalchemy import text

from app.core.database import SessionLocal
from app.models.music import MusicArtist
from app.services.music_library_service import CatalogAdminService
from app.utils.time import utcnow
from sync_nested_catalog import save_backup_file, take_folder_snapshot


# One row per card: how much audio sits in the card's own folder and one level
# below it. This is exactly what classify_node_kind needs.
_COUNT_SQL = text("""
    SELECT a.id,
           a.folder_id,
           a.kind,
           a.parent_id,
           a.display_name,
           (SELECT count(*) FROM vfiles v
             WHERE v.folder_id = a.folder_id
               AND v.is_trash = FALSE
               AND v.mime_type LIKE 'audio/%') AS audio,
           (SELECT count(*) FROM vfiles v
              JOIN folders f ON f.id = v.folder_id
             WHERE f.parent_id = a.folder_id
               AND f.is_trash = FALSE
               AND v.is_trash = FALSE
               AND v.mime_type LIKE 'audio/%') AS child_audio
      FROM music_artists a
     ORDER BY a.id
""")


def collect_changes(db):
    """(changes, parent_by_id) for every nested card whose kind is wrong.

    `changes` holds `(card_row, desired_kind)`; `parent_by_id` maps card id →
    parent id for the whole catalog, so the caller can find the tree roots a
    change belongs to (only those trees need a snapshot).
    """
    changes = []
    parent_by_id = {}
    for row in db.execute(_COUNT_SQL).all():
        parent_by_id[row.id] = row.parent_id
        # Root cards keep the kind the operator chose for the tree; only the
        # nodes created by the recursive sync are derived from folder content.
        if row.parent_id is None:
            continue
        desired = CatalogAdminService.classify_node_kind(
            1,
            has_direct_audio=row.audio > 0,
            has_child_audio=row.child_audio > 0,
        )
        if desired != row.kind:
            changes.append((row, desired))
    return changes, parent_by_id


def root_ids_for(changes, parent_by_id):
    """Catalog tree roots touched by the given changes (cycle-safe)."""
    roots = set()
    for row, _ in changes:
        current = row.id
        for _ in range(64):
            parent = parent_by_id.get(current)
            if parent is None:
                roots.add(current)
                break
            current = parent
    return sorted(roots)


def main():
    parser = argparse.ArgumentParser(description="Renormalize catalog card kinds")
    parser.add_argument("--apply", action="store_true", help="Write changes (default is a dry run)")
    parser.add_argument("--no-backup", action="store_true", help="Skip the pre-change snapshot")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        changes, parent_by_id = collect_changes(db)
        transitions = Counter((row.kind, desired) for row, desired in changes)

        print(f"Nested cards needing a new kind: {len(changes)}")
        for (old, new), count in sorted(transitions.items(), key=lambda kv: -kv[1]):
            print(f"  {old:16} -> {new:16} {count}")

        if not changes:
            print("Nothing to do — kinds already match the content rule.")
            return

        if not args.apply:
            print("\n[DRY-RUN] Nothing written. Re-run with --apply to commit.")
            return

        backup_path = None
        if not args.no_backup:
            root_ids = root_ids_for(changes, parent_by_id)
            print(f"\nSnapshotting {len(root_ids)} affected catalog tree(s) before the change...")
            backup_path = save_backup_file([take_folder_snapshot(db, rid) for rid in root_ids])
            print(f"Backup snapshot saved to: {backup_path.relative_to(BASE_DIR)}")

        changed = 0
        for row, desired in changes:
            card = db.get(MusicArtist, row.id)
            if card is None or card.kind == desired:
                continue
            card.kind = desired
            card.updated_at = utcnow()
            changed += 1
        db.commit()
        print(f"\nUpdated cards: {changed}")

        if backup_path:
            print("\n" + "=" * 70)
            print("ROLLBACK: to restore the previous kinds run")
            print(f"  python scripts/rollback_nested_catalog.py --backup-file {backup_path.relative_to(BASE_DIR)}")
            print("=" * 70)
    except Exception as exc:  # noqa: BLE001 — a failed run must not leave a half-written catalog
        db.rollback()
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
