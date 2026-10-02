"""
Shared test fixtures.

The external provider disk cache (lyrics / last.fm) persists between calls and
across runs. Every test must see a cold cache and must not write into the real
`cache/external/` directory, or negative entries cached by one test leak into
the next and network stubs silently never fire.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.core.disk_cache import external_cache  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from password_guard import (  # noqa: E402
    DB_PASSWORD_MESSAGE,
    OWNER_ADMIN_PASSWORD,
    PasswordMutationError,
    check_env_unchanged,
    snapshot_admin_hash_line,
)


@pytest.fixture(autouse=True)
def isolate_external_disk_cache(tmp_path, monkeypatch):
    """Point the disk-cache singleton at a per-test empty directory."""
    monkeypatch.setattr(external_cache, "root", tmp_path / "external")
    return tmp_path / "external"


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
    """Fail if the live admin row no longer accepts the owner's password."""
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
