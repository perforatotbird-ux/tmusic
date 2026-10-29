"""
T-Music Database Engine and Session Management
"""
import re
import time
import logging
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings

logger = logging.getLogger(__name__)

_TEST_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

_connect_args = {}
if settings.TEST_DATABASE_SCHEMA:
    schema = settings.TEST_DATABASE_SCHEMA.strip().lower()
    if not _TEST_SCHEMA_RE.fullmatch(schema) or schema == "public":
        raise RuntimeError("TEST_DATABASE_SCHEMA must be a valid non-public schema name.")
    # psycopg2 option: keep every unqualified table in the disposable schema.
    _connect_args["options"] = f"-csearch_path={schema}"

engine = create_engine(
    settings.DATABASE_URL,
    pool_size=15,
    max_overflow=25,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI Dependency for database session with automatic transaction management."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def wait_for_database(timeout: int = 30) -> bool:
    """Verify database connectivity on application startup."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                return True
        except Exception as e:
            logger.warning("Database not ready, retrying in 2 seconds... (%s)", e)
            time.sleep(2)
    return False


_PERFORMANCE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_music_artists_display_name ON music_artists (display_name)",
    "CREATE INDEX IF NOT EXISTS idx_music_artists_search_name ON music_artists (search_name)",
    "CREATE INDEX IF NOT EXISTS idx_music_albums_title ON music_albums (title)",
    "CREATE INDEX IF NOT EXISTS idx_music_albums_artist_year ON music_albums (artist_id, year)",
    "CREATE INDEX IF NOT EXISTS idx_vfiles_trash_mime ON vfiles (is_trash, mime_type)",
    "CREATE INDEX IF NOT EXISTS idx_vfiles_folder_audio ON vfiles (folder_id, is_trash, mime_type)",
)


def _run_schema_step(name: str, *statements: str) -> None:
    """Execute additive schema statements; log instead of crashing on failure.

    Schema self-healing must never prevent startup (a missing index is a
    performance issue, not a boot failure).
    """
    try:
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
        logger.info("%s ensured.", name)
    except Exception as e:
        logger.warning("Failed to ensure %s: %s", name.lower(), e)


def ensure_performance_indexes() -> None:
    """Create query-path indexes on existing databases (create_all does not ALTER)."""
    _run_schema_step("Performance indexes", *_PERFORMANCE_INDEXES)


def ensure_trigram_indexes() -> None:
    """Trigram GIN indexes for case-insensitive LIKE search.

    Best-effort: CREATE EXTENSION needs a superuser, so on installs where the
    app role is not one this logs a warning and search keeps working via
    sequential scan (fine for small catalogs, GIN kicks in at scale).
    """
    _run_schema_step(
        "Trigram indexes",
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "CREATE INDEX IF NOT EXISTS idx_artists_name_trgm "
        "ON music_artists USING gin (lower(display_name) gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS idx_albums_title_trgm "
        "ON music_albums USING gin (lower(title) gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS idx_vfiles_name_trgm "
        "ON vfiles USING gin (lower(name) gin_trgm_ops) "
        "WHERE is_trash = false AND mime_type LIKE 'audio/%'",
    )


def ensure_auth_schema() -> None:
    """Additive schema patch for existing databases (no migration framework in use).

    Currently ensures app_users.token_version used for global session revocation.
    Safe to run on every startup: ADD COLUMN IF NOT EXISTS.
    """
    _run_schema_step(
        "Auth schema",
        "ALTER TABLE app_users "
        "ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE app_users "
        "ADD COLUMN IF NOT EXISTS subsonic_token_enc VARCHAR(512)",
        "ALTER TABLE app_users "
        "ADD COLUMN IF NOT EXISTS role VARCHAR(32) NOT NULL DEFAULT 'user'",
        "ALTER TABLE app_users "
        "ADD COLUMN IF NOT EXISTS avatar_key VARCHAR(128)",
        "ALTER TABLE app_users "
        "ADD COLUMN IF NOT EXISTS avatar_version INTEGER NOT NULL DEFAULT 0",
        "UPDATE app_users SET role = 'admin' WHERE username = 'admin' AND (role IS NULL OR role = '' OR role = 'user')",
    )


def ensure_catalog_schema() -> None:
    """Additive schema patch for the music catalog hierarchy.

    music_artists.parent_id enables the admin card-nesting feature (drag an
    artist/collection card into another card). vfiles.custom_image_key lets the
    admin assign artwork to an individual track, exactly like artists/albums.
    Safe to run on every startup: ADD COLUMN IF NOT EXISTS + conditional index
    creation.
    """
    _run_schema_step(
        "Catalog schema",
        "ALTER TABLE music_artists "
        "ADD COLUMN IF NOT EXISTS parent_id INTEGER REFERENCES music_artists(id) ON DELETE SET NULL",
        "CREATE INDEX IF NOT EXISTS idx_music_artists_parent ON music_artists (parent_id)",
        "ALTER TABLE vfiles "
        "ADD COLUMN IF NOT EXISTS custom_image_key VARCHAR(128)",
        "CREATE INDEX IF NOT EXISTS idx_vfiles_custom_image_key ON vfiles (custom_image_key)",
    )


def ensure_feature_tables() -> None:
    """Create additive feature tables (playback + playlists + settings) on startup."""
    try:
        from app.models.base import Base
        from app.models.auth import AdminAuditEvent, AppSetting, BackupRun
        from app.models.playback import ListeningHistory, PlaybackPosition, UserFavorite
        from app.models.playlist import Playlist, PlaylistItem
        from app.models.share import ShareLink

        Base.metadata.create_all(
            bind=engine,
            tables=[
                PlaybackPosition.__table__,
                ListeningHistory.__table__,
                UserFavorite.__table__,
                Playlist.__table__,
                PlaylistItem.__table__,
                AppSetting.__table__,
                AdminAuditEvent.__table__,
                BackupRun.__table__,
                ShareLink.__table__,
            ],
        )
        logger.info("Playback/playlist/share/settings tables ensured.")
    except Exception as e:
        logger.warning("Failed to ensure feature tables: %s", e)
