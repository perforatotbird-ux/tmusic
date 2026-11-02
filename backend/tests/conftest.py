"""
Shared test fixtures.

The external provider disk cache (lyrics / last.fm) persists between calls and
across runs. Every test must see a cold cache and must not write into the real
`cache/external/` directory, or negative entries cached by one test leak into
the next and network stubs silently never fire.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.core.config import settings  # noqa: E402

# Must happen before app.core.database is imported by any test module: the
# engine embeds the search_path at creation time.
_TEST_SCHEMA = (os.environ.get("TMUSIC_TEST_SCHEMA") or "").strip().lower()
if _TEST_SCHEMA:
    settings.TEST_DATABASE_SCHEMA = _TEST_SCHEMA

from app.core.disk_cache import external_cache  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from password_guard import (  # noqa: E402
    DB_PASSWORD_MESSAGE,
    OWNER_ADMIN_PASSWORD,
    PasswordMutationError,
    check_env_unchanged,
    snapshot_admin_hash_line,
)


@pytest.fixture(scope="session", autouse=True)
def isolated_test_schema():
    """Run the whole suite in a disposable PostgreSQL schema.

    The historical suite ran against the live `public` schema and could mutate
    real catalog/settings rows. `TMUSIC_TEST_SCHEMA` selects a throw-away
    schema: it is created empty, all tables are created there, and the schema is
    dropped afterwards. Running without it against the live DB is refused unless
    TMUSIC_ALLOW_LIVE_DB_TESTS=1 is set explicitly.
    """
    if not _TEST_SCHEMA:
        if os.environ.get("TMUSIC_ALLOW_LIVE_DB_TESTS") == "1":
            yield
            return
        pytest.exit(
            "Refusing to run backend tests against the live database. "
            "Set TMUSIC_TEST_SCHEMA=<schema> (recommended) or "
            "TMUSIC_ALLOW_LIVE_DB_TESTS=1 (explicit, destructive).",
            returncode=2,
        )

    import psycopg2

    from app.core.database import engine
    from app.models.base import Base
    import app.models  # noqa: F401 — register every table on Base.metadata

    with psycopg2.connect(settings.DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{_TEST_SCHEMA}"')
        conn.commit()

    Base.metadata.create_all(bind=engine)
    # Auth tests expect the owner-fixed admin credential; bootstrap it into the
    # disposable schema exactly like a fresh install would.
    from app.core.database import SessionLocal
    from app.services.bootstrap import ensure_admin_bootstrap

    with SessionLocal() as db:
        ensure_admin_bootstrap(db)
        if not settings.ADMIN_PASSWORD_HASH:
            # CI has no music.env: provision the owner-fixed credential locally
            # in the disposable schema only.
            from app.core.security import hash_password
            from app.models.auth import AppUser

            if db.query(AppUser).filter(AppUser.username == "admin").first() is None:
                db.add(AppUser(username="admin", password_hash=hash_password("123"), is_active=True, role="admin"))
                db.commit()
    try:
        yield
    finally:
        engine.dispose()
        if os.environ.get("TMUSIC_KEEP_TEST_SCHEMA") != "1":
            with psycopg2.connect(settings.DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(f'DROP SCHEMA IF EXISTS "{_TEST_SCHEMA}" CASCADE')
                conn.commit()


@pytest.fixture(autouse=True)
def isolate_external_disk_cache(tmp_path, monkeypatch):
    """Point the disk-cache singleton at a per-test empty directory."""
    monkeypatch.setattr(external_cache, "root", tmp_path / "external")
    return tmp_path / "external"


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """In-memory lockouts must not leak between tests (e.g. Subsonic auth)."""
    from app.core.rate_limiter import limiter

    with limiter._lock:
        limiter._requests.clear()
        limiter._failed_attempts.clear()
        limiter._lockouts.clear()
    yield
    with limiter._lock:
        limiter._requests.clear()
        limiter._failed_attempts.clear()
        limiter._lockouts.clear()


@pytest.fixture(autouse=True)
def protect_admin_password():
    """Owner decision 2026-10-02: the panel password is always "123".

    Two independent sources get clobbered by test runs, so both are guarded:

    * the ADMIN_PASSWORD_HASH line in music.env — the single source of truth;
    * the admin row in the LIVE database, which several tests used to overwrite
      with their own password and thereby lock the owner out of the panel.

    The env line is checked on disk. The DB row is checked with verify_password,
    which needs the plaintext owner password as an argument — it is never
    written to a file, the repository or the Obsidian notes.
    """
    before = snapshot_admin_hash_line()
    yield
    check_env_unchanged(before)
    assert_db_admin_password_intact()


def assert_db_admin_password_intact() -> None:
    """Fail if the live admin row no longer accepts the owner's password.

    In an isolated test schema the admin row is synthetic and unrelated to the
    owner's panel password, so this guard only applies to the live database.
    """
    if settings.TEST_DATABASE_SCHEMA:
        return
    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.core.security import verify_password
    from app.models.auth import AppUser

    try:
        with SessionLocal() as db:
            user = db.execute(
                select(AppUser).where(AppUser.username == "admin")
            ).scalar_one_or_none()
    except Exception as exc:  # DB unreachable — not a password failure
        pytest.skip(f"database unavailable, skipping DB password guard: {exc}")

    if user is None:
        # No admin row at all: bootstrap provisions it from music.env on boot.
        return

    if not verify_password(OWNER_ADMIN_PASSWORD, user.password_hash or ""):
        raise PasswordMutationError(
            f"{DB_PASSWORD_MESSAGE}\nRun: python scripts/restore_admin_password.py"
        )

