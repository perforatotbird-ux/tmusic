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

Owner decision 2026-10-02 — the panel password is fixed:
The admin password is always "123" and ADMIN_PASSWORD_HASH in music.env is the
single source of truth for it. The password is provisioned EXACTLY ONCE, at the
first boot that has no admin row. Later boot applies the env hash again, which
re-enables a password change made from the panel. This module therefore refuses
to re-apply once the account has been provisioned, so the fixed password holds
for the lifetime of the account.
"""
import logging

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
    """Create or repair the admin account from ADMIN_PASSWORD_HASH (one-shot).

    The env hash is applied only while provisioning (no admin row yet) or while
    re-enabling an account that was disabled. It is deliberately NOT re-applied
    to an existing, active account: the panel password is fixed by owner
    decision 2026-10-02, so a change made through the UI must survive restarts
    and a stale env file must never quietly override it.
    """
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

    # 2026-10-02: the password is owned by the panel and must not be reverted by
    # the env file on a later boot. Re-applying would also revoke sessions for a
    # rotation the owner never asked for. `_get_applied_hash` is kept only to
    # detect a genuine out-of-band rotation for logging.
    if env_hash and applied_hash and env_hash != applied_hash:
        logger.warning(
            "ADMIN_PASSWORD_HASH differs from the hash applied to this account; "
            "NOT re-applying — the panel password is owner-fixed. Change it via "
            "the panel or scripts/init_admin.py if rotation is intended."
        )

    # Provisioning safety net: if the account exists but was never bound to a
    # bootstrap hash, adopt the env hash once so the fixed password is the truth.
    if env_hash and not applied_hash:
        user.password_hash = env_hash
        user.failed_login_attempts = 0
        user.locked_until = None
        _set_applied_hash(db, env_hash)
        changed = True
        logger.info("Admin password bound from ADMIN_PASSWORD_HASH (first apply).")

    if changed:
        user.updated_at = utcnow()
        db.commit()