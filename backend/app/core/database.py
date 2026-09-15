"""
T-Music Database Engine and Session Management
"""
import time
import logging
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.DATABASE_URL,
    pool_size=15,
    max_overflow=25,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True
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


def ensure_performance_indexes() -> None:
    """Create query-path indexes on existing databases (create_all does not ALTER)."""
    try:
        with engine.begin() as conn:
            for stmt in _PERFORMANCE_INDEXES:
                conn.execute(text(stmt))
        logger.info("Performance indexes ensured.")
    except Exception as e:
        logger.warning("Failed to ensure performance indexes: %s", e)


def ensure_auth_schema() -> None:
    """Additive schema patch for existing databases (no migration framework in use).

    Currently ensures app_users.token_version used for global session revocation.
    Safe to run on every startup: ADD COLUMN IF NOT EXISTS.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE app_users "
                "ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0"
            ))
        logger.info("Auth schema ensured.")
    except Exception as e:
        logger.warning("Failed to ensure auth schema: %s", e)
