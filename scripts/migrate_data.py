"""
T-Music Migration Script
Transfers the Music VFS hierarchy, audio vfiles, music metadata, tg_accounts, and cover caches
from the source CloudByte database (tgvfs) into the dedicated T-Music database (tgmusic).

READ-ONLY access to source tgvfs. ZERO modification of CloudByte files or app.env.
"""
import os
import sys
import shutil
import logging
from pathlib import Path
from typing import Set, List, Dict, Any
from dotenv import dotenv_values

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

# Add tmusic backend to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
TMUSIC_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = TMUSIC_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.models.base import Base
from app.models.vfs import TGAccount, Folder, FolderClosure, VFile
from app.models.music import MusicArtist, MusicAlbum
from app.models.auth import AppUser

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("tmusic.migration")

# Locate source app.env in CloudByte root
CLOUDBYTE_ROOT = TMUSIC_ROOT.parent
SOURCE_ENV_PATH = CLOUDBYTE_ROOT / "app.env"


def get_db_urls() -> tuple[str, str]:
    if not SOURCE_ENV_PATH.exists():
        raise FileNotFoundError(f"Source env not found at {SOURCE_ENV_PATH}")

    src_env = dotenv_values(SOURCE_ENV_PATH)
    src_db_url = src_env.get("DATABASE_URL")
    if not src_db_url:
        raise ValueError("DATABASE_URL missing in source app.env")

    # Target database url: replace database name with tgmusic
    if "/" in src_db_url:
        parts = src_db_url.rsplit("/", 1)
        tgt_db_url = f"{parts[0]}/tgmusic"
    else:
        raise ValueError(
            "Cannot derive target DATABASE_URL from source app.env — "
            "no implicit credentials are used. Fix the source DATABASE_URL."
        )

    return src_db_url, tgt_db_url


def _get_session_secret() -> str | None:
    """SESSION_SECRET from tmusic music.env — used to encrypt TG creds at rest."""
    env_path = TMUSIC_ROOT / "music.env"
    if env_path.exists():
        return dotenv_values(env_path).get("SESSION_SECRET")
    return None


def migrate_tg_accounts(src_db, tgt_db):
    """
    Copies TGAccount records from tgvfs to tgmusic.
    SECURITY (2026-09 audit): api_hash and session_string are Fernet-encrypted
    with SESSION_SECRET before being written to the target database.
    """
    logger.info("Migrating TG cluster accounts...")
    from app.tg.manager import ensure_encrypted

    secret = _get_session_secret()
    if not secret:
        logger.warning(
            "SESSION_SECRET not set in music.env — TG credentials will be "
            "copied WITHOUT at-rest encryption. Set it and re-run."
        )

    def _protect(value):
        sealed, _ = ensure_encrypted(value, secret)
        return sealed

    accounts = src_db.execute(
        text("SELECT id, name, api_id, api_hash, session_string, is_active, is_primary, status, last_used, created_at FROM tg_accounts")
    ).fetchall()

    for acc in accounts:
        enc_api_hash = _protect(acc[3])
        enc_session = _protect(acc[4])
        existing = tgt_db.execute(select(TGAccount).where(TGAccount.id == acc[0])).scalar_one_or_none()
        if existing:
            existing.name = acc[1]
            existing.api_id = acc[2]
            existing.api_hash = enc_api_hash
            existing.session_string = enc_session
            existing.is_active = acc[5]
            existing.is_primary = acc[6]
            existing.status = acc[7]
            existing.last_used = acc[8]
            existing.created_at = acc[9]
        else:
            tgt_db.add(TGAccount(
                id=acc[0],
                name=acc[1],
                api_id=acc[2],
                api_hash=enc_api_hash,
                session_string=enc_session,
                is_active=acc[5],
                is_primary=acc[6],
                status=acc[7],
                last_used=acc[8],
                created_at=acc[9]
            ))
    tgt_db.commit()
    logger.info("Migrated %d TG cluster accounts successfully (encrypted at rest: %s).",
                len(accounts), bool(secret))


def migrate_data(full_wipe: bool = False):
    src_url, tgt_url = get_db_urls()
    logger.info("Connecting to databases...")

    src_engine = create_engine(src_url, pool_pre_ping=True)
    tgt_engine = create_engine(tgt_url, pool_pre_ping=True)

    # 1. Initialize schema in target DB
    logger.info("Ensuring schema and tables in target database tgmusic...")
    Base.metadata.create_all(bind=tgt_engine)
    logger.info("Schema verified.")

    SrcSession = sessionmaker(bind=src_engine)
    TgtSession = sessionmaker(bind=tgt_engine)

    with SrcSession() as src_db, TgtSession() as tgt_db:
        # Always migrate tg_accounts
        migrate_tg_accounts(src_db, tgt_db)

        # Check if vfiles already migrated and full_wipe is not requested
        vfiles_count = tgt_db.execute(text("SELECT count(*) FROM vfiles")).scalar()
        if vfiles_count and vfiles_count > 0 and not full_wipe:
            logger.info("Target database already contains %d vfiles. Skipping file re-migration.", vfiles_count)
            return

        # 2. Find Music root folder in source DB
        music_root_row = src_db.execute(
            text("SELECT id, name FROM folders WHERE name = 'Music' AND is_trash = FALSE LIMIT 1")
        ).fetchone()

        if not music_root_row:
            logger.error("Folder 'Music' not found in source database!")
            return

        music_root_id = music_root_row[0]
        logger.info("Found Music root folder: ID=%d, Name='%s'", music_root_id, music_root_row[1])

        # 3. Retrieve all descendant folder IDs with their depth from Music root
        closure_rows = src_db.execute(
            text("""
                SELECT descendant_id, min(depth) as d
                FROM folder_closures 
                WHERE ancestor_id = :aid 
                GROUP BY descendant_id
                ORDER BY d ASC
            """),
            {"aid": music_root_id}
        ).fetchall()

        folder_depths: Dict[int, int] = {r[0]: r[1] for r in closure_rows}
        folder_depths[music_root_id] = 0
        folder_ids = list(folder_depths.keys())
        logger.info("Total folders in Music subtree: %d", len(folder_ids))

        # 4. Clean target tables first if full wipe
        logger.info("Cleaning existing target data...")
        tgt_db.execute(text("TRUNCATE TABLE folder_closures, music_albums, music_artists, vfiles, folders CASCADE;"))
        tgt_db.commit()

        # 5. Migrate folders in order of depth (parents first)
        logger.info("Migrating folders in hierarchical order...")
        folder_rows = src_db.execute(
            text("""
                SELECT id, name, parent_id, is_trash, is_starred, color, note,
                       custom_icon, cached_total_size, cached_file_count, created_at, updated_at
                FROM folders WHERE id = ANY(:fids)
            """),
            {"fids": folder_ids}
        ).fetchall()

        folder_map = {r[0]: r for r in folder_rows}
        # Sort by depth ASC so parents are inserted before children
        sorted_fids = sorted(folder_ids, key=lambda fid: folder_depths.get(fid, 9999))

        for fid in sorted_fids:
            row = folder_map.get(fid)
            if not row:
                continue
            # For the root 'Music' folder, set parent_id = None
            parent_id = None if fid == music_root_id else row[2]
            tgt_db.add(Folder(
                id=row[0],
                name=row[1],
                parent_id=parent_id,
                is_trash=row[3],
                is_starred=row[4],
                color=row[5],
                note=row[6],
                custom_icon=row[7],
                cached_total_size=row[8],
                cached_file_count=row[9],
                created_at=row[10],
                updated_at=row[11]
            ))
        tgt_db.commit()
        logger.info("Migrated %d folders successfully.", len(sorted_fids))

        # 6. Migrate folder_closures
        logger.info("Migrating folder closures...")
        closure_data = src_db.execute(
            text("""
                SELECT ancestor_id, descendant_id, depth
                FROM folder_closures
                WHERE ancestor_id = ANY(:fids) AND descendant_id = ANY(:fids)
            """),
            {"fids": folder_ids}
        ).fetchall()

        for row in closure_data:
            tgt_db.add(FolderClosure(
                ancestor_id=row[0],
                descendant_id=row[1],
                depth=row[2]
            ))
        tgt_db.commit()
        logger.info("Migrated %d folder closures.", len(closure_data))

        # 7. Migrate VFiles in batches
        logger.info("Migrating audio vfiles in batches...")
        batch_size = 5000
        offset = 0
        total_vfiles = 0

        while True:
            vfile_rows = src_db.execute(
                text("""
                    SELECT id, name, size, mime_type, tg_message_id, msg_ids,
                           tg_file_id, folder_id, is_starred, is_trash, pending_deletion,
                           encryption_salt, encryption_header, is_encrypted, note,
                           custom_icon, account_map, created_at, updated_at
                    FROM vfiles
                    WHERE folder_id = ANY(:fids) AND is_trash = FALSE
                    ORDER BY id
                    LIMIT :limit OFFSET :offset
                """),
                {"fids": folder_ids, "limit": batch_size, "offset": offset}
            ).fetchall()

            if not vfile_rows:
                break

            for r in vfile_rows:
                tgt_db.add(VFile(
                    id=r[0],
                    name=r[1],
                    size=r[2],
                    mime_type=r[3],
                    tg_message_id=r[4],
                    msg_ids=r[5],
                    tg_file_id=r[6],
                    folder_id=r[7],
                    is_starred=r[8],
                    is_trash=r[9],
                    pending_deletion=r[10],
                    encryption_salt=r[11],
                    encryption_header=r[12],
                    is_encrypted=r[13],
                    note=r[14],
                    custom_icon=r[15],
                    account_map=r[16] if isinstance(r[16], dict) else {},
                    created_at=r[17],
                    updated_at=r[18]
                ))
            tgt_db.commit()
            total_vfiles += len(vfile_rows)
            offset += batch_size
            logger.info("Migrated %d vfiles so far...", total_vfiles)

        logger.info("Total vfiles migrated: %d", total_vfiles)

        # 8. Migrate MusicArtists
        logger.info("Migrating music artists...")
        artist_rows = src_db.execute(
            text("""
                SELECT id, folder_id, kind, display_name, search_name, mbid,
                       image_url, bio, wikipedia_url, confidence, locked,
                       custom_image_key, enriched_at, created_at, updated_at
                FROM music_artists
                WHERE folder_id = ANY(:fids)
            """),
            {"fids": folder_ids}
        ).fetchall()

        migrated_artist_ids = set()
        for r in artist_rows:
            tgt_db.add(MusicArtist(
                id=r[0],
                folder_id=r[1],
                kind=r[2],
                display_name=r[3],
                search_name=r[4],
                mbid=r[5],
                image_url=r[6],
                bio=r[7],
                wikipedia_url=r[8],
                confidence=r[9],
                locked=r[10],
                custom_image_key=r[11],
                enriched_at=r[12],
                created_at=r[13],
                updated_at=r[14]
            ))
            migrated_artist_ids.add(r[0])
        tgt_db.commit()
        logger.info("Migrated %d music artists.", len(artist_rows))

        # 9. Migrate MusicAlbums
        logger.info("Migrating music albums...")
        album_rows = src_db.execute(
            text("""
                SELECT id, folder_id, artist_id, title, year, mbid,
                       cover_url, confidence, custom_image_key,
                       enriched_at, created_at, updated_at
                FROM music_albums
                WHERE artist_id = ANY(:aids) OR folder_id = ANY(:fids)
            """),
            {"aids": list(migrated_artist_ids), "fids": folder_ids}
        ).fetchall()

        for r in album_rows:
            tgt_db.add(MusicAlbum(
                id=r[0],
                folder_id=r[1],
                artist_id=r[2],
                title=r[3],
                year=r[4],
                mbid=r[5],
                cover_url=r[6],
                confidence=r[7],
                custom_image_key=r[8],
                enriched_at=r[9],
                created_at=r[10],
                updated_at=r[11]
            ))
        tgt_db.commit()
        logger.info("Migrated %d music albums.", len(album_rows))

        # 10. Admin user provisioning
        # SECURITY (2026-09 audit, C-2): NO default credentials are created here.
        # The application bootstrap (app/services/bootstrap.py) provisions the
        # admin account from ADMIN_PASSWORD_HASH (bcrypt) on first startup,
        # or run scripts/init_admin.py to set credentials interactively.
        existing_admin = tgt_db.execute(select(AppUser).where(AppUser.username == "admin")).scalar_one_or_none()
        if not existing_admin:
            logger.warning(
                "No admin account migrated. Provision one via ADMIN_PASSWORD_HASH "
                "in music.env or scripts/init_admin.py — default credentials are "
                "no longer created by design."
            )

    # 11. Copy Caches & Covers
    logger.info("Copying cache files from CloudByte to T-Music...")
    src_cache = CLOUDBYTE_ROOT / "cache"
    tgt_cache = TMUSIC_ROOT / "cache"

    for sub in ["music_images", "music_covers"]:
        src_sub = src_cache / sub
        tgt_sub = tgt_cache / sub
        tgt_sub.mkdir(parents=True, exist_ok=True)
        if src_sub.exists():
            files = [f for f in src_sub.iterdir() if f.is_file()]
            logger.info("Copying %d files for %s...", len(files), sub)
            for f in files:
                dest = tgt_sub / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)
            logger.info("Finished copying %s.", sub)

    logger.info("=== Migration Completed Successfully! ===")


if __name__ == "__main__":
    force_wipe = "--full-wipe" in sys.argv
    migrate_data(full_wipe=force_wipe)
