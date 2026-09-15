"""
T-Music Admin Bootstrap Service
Self-healing admin account provisioning executed on application startup.

Why this exists (2026-09 security audit, finding C-2):
Historical installs shipped with hardcoded default credentials
(admin / admin123, PIN 1234) stored as unsalted/salted SHA-256.
This bootstrap guarantees that on every startup:
  1. An active "admin" account exists.
  2. Its password matches the bcrypt hash provided via the
     ADMIN_PASSWORD_HASH environment variable (if configured).
  3. Legacy (non-bcrypt) PIN hashes are wiped so default-PIN logins
      stop working; deliberately-set bcrypt PINs are preserved.

The env var is the source of truth while present — remove it from
music.env after the first successful boot if you prefer managing the
password exclusively through POST /api/auth/change-password.
"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.auth import AppUser
from app.utils.time import utcnow

logger = logging.getLogger("tmusic.bootstrap")

ADMIN_USERNAME = "admin"


def _is_bcrypt(hash_value: str) -> bool:
    return bool(hash_value) and hash_value.startswith("$2")


def ensure_admin_bootstrap(db: Session) -> None:
    """Create or repair the admin account from ADMIN_PASSWORD_HASH."""
    env_hash = (settings.ADMIN_PASSWORD_HASH or "").strip()

    if env_hash and not _is_bcrypt(env_hash):
        logger.error(
            "ADMIN_PASSWORD_HASH is set but is not a bcrypt hash — ignoring it. "
            "Generate one with: python scripts/init_admin.py --hash-only <password>"
        )
        env_hash = ""

    user = db.execute(
        select(AppUser).where(AppUser.username == ADMIN_USERNAME)
    ).scalar_one_or_none()

    if user is None:
        if not env_hash:
            logger.warning(
                "No admin account found and ADMIN_PASSWORD_HASH is not set. "
                "The panel is unreachable until you provision credentials via "
                "scripts/init_admin.py."
            )
            return
        user = AppUser(
            username=ADMIN_USERNAME,
            password_hash=env_hash,
            pin_hash=None,
            is_active=True,
            failed_login_attempts=0,
            locked_until=None,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(user)
        db.commit()
        logger.info("Admin account created from ADMIN_PASSWORD_HASH.")
        return

    changed = False

    if not user.is_active:
        user.is_active = True
        changed = True
        logger.warning("Admin account was inactive — re-activated by bootstrap.")

    if env_hash and user.password_hash != env_hash:
        user.password_hash = env_hash
        user.failed_login_attempts = 0
        user.locked_until = None
        # Password material changed outside the normal flow — revoke old sessions.
        user.token_version = (user.token_version or 0) + 1
        changed = True
        logger.info("Admin password updated from ADMIN_PASSWORD_HASH (sessions revoked).")

    # Wipe legacy (non-bcrypt) PIN hashes — these are the default-PIN 1234 era.
    if user.pin_hash and not _is_bcrypt(user.pin_hash):
        user.pin_hash = None
        changed = True
        logger.warning(
            "Removed legacy (non-bcrypt) admin PIN hash. "
            "Set a new PIN via POST /api/auth/change-pin if needed."
        )

    if changed:
        user.updated_at = utcnow()
        db.commit()
