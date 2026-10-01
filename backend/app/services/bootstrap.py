"""
T-Music Admin Bootstrap Service
Self-healing admin account provisioning executed on application startup.

Why this exists (2026-09 security audit, finding C-2):
Historical installs shipped with hardcoded default credentials
(admin / admin123) stored as unsalted/salted SHA-256.
This bootstrap guarantees that on startup:
  1. An active "admin" account exists.
  2. Its password matches the Argon2id/bcrypt hash provided via the
     ADMIN_PASSWORD_HASH environment variable (if configured).

One-shot semantics (2026-09 audit fix, minor 1):
ADMIN_PASSWORD_HASH used to be re-applied on EVERY boot, silently reverting
passwords changed through the panel and revoking sessions. The env var is now
consumed exactly once per hash value: the applied hash is remembered in the
app_settings table (key `admin_bootstrap_hash`), and on later boots it only
re-applies when the env value CHANGES (deliberate rotation via music.env).
Removing the variable from music.env after the first boot is still the
recommended hygiene, but is no longer required to keep panel password changes.
"""
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import is_supported_password_hash
from app.models.auth import AppSetting, AppUser
from app.utils.time import utcnow

logger = logging.getLogger("tmusic.bootstrap")

ADMIN_USERNAME = "admin"
_APPLIED_HASH_KEY = "admin_bootstrap_hash"


def _get_applied_hash(db: Session) -> str:
    row = db.get(AppSetting, _APPLIED_HASH_KEY)
    return row.value if row and row.value else ""


def _set_applied_hash(db: Session, hash_value: str) -> None:
    row = db.get(AppSetting, _APPLIED_HASH_KEY)
    if row is None:
        row = AppSetting(key=_APPLIED_HASH_KEY, value=hash_value)
        db.add(row)
    else:
        row.value = hash_value
        row.updated_at = utcnow()


def ensure_admin_bootstrap(db: Session) -> None:
    """Create or repair the admin account from ADMIN_PASSWORD_HASH (one-shot)."""
    env_hash = (settings.ADMIN_PASSWORD_HASH or "").strip()

    if env_hash and not is_supported_password_hash(env_hash):
        logger.error(
            "ADMIN_PASSWORD_HASH is not a supported Argon2id/bcrypt hash — ignoring it. "
            "Generate one with: python scripts/init_admin.py --hash-only <password>"
        )
        env_hash = ""

    applied_hash = _get_applied_hash(db) if env_hash else ""

    user = db.execute(
        select(AppUser).where(AppUser.username == ADMIN_USERNAME).with_for_update()
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
            is_active=True,
            failed_login_attempts=0,
            locked_until=None,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(user)
        _set_applied_hash(db, env_hash)
        db.commit()
        logger.info("Admin account created from ADMIN_PASSWORD_HASH.")
        return

    changed = False

    if not user.is_active:
        user.is_active = True
        changed = True
        logger.warning("Admin account was inactive — re-activated by bootstrap.")

    # One-shot application: only (re)apply the env hash when it differs from
    # the hash we already consumed for this account. A panel-side password
    # change (which updates password_hash but not admin_bootstrap_hash) is
    # therefore preserved across restarts; changing the env value still works
    # as an explicit out-of-band rotation.
    if env_hash and env_hash != applied_hash:
        user.password_hash = env_hash
        user.failed_login_attempts = 0
        user.locked_until = None
        # Password material changed outside the normal flow — revoke old sessions.
        user.token_version = (user.token_version or 0) + 1
        _set_applied_hash(db, env_hash)
        changed = True
        logger.info(
            "Admin password updated from ADMIN_PASSWORD_HASH (env rotation; sessions revoked)."
        )

    if changed:
        user.updated_at = utcnow()
        db.commit()
