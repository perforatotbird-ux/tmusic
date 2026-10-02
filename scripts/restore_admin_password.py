"""Restore the admin account to the owner-fixed password ("123").

Why this exists (2026-10-02): several test modules used to write their own
password into the LIVE admin row and commit it, which left the owner unable to
log in. The tests now use a restoring sandbox, but if the row is ever clobbered
again (an older branch, a manual edit, a crash mid-test), this puts it back.

Safe to run repeatedly: if the password is already correct it changes nothing.

    python scripts/restore_admin_password.py

Also syncs app_settings.admin_bootstrap_hash, because ensure_admin_bootstrap
treats a differing marker as "never applied" and would otherwise re-apply the
env hash on the next boot.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import text  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password, verify_password  # noqa: E402
from app.models.auth import AppUser  # noqa: E402

OWNER_PASSWORD = "123"
USERNAME = "admin"
APPLIED_KEY = "admin_bootstrap_hash"


def env_hash() -> str:
    """The hash music.env declares for the admin password (never printed)."""
    env = ROOT / "music.env"
    if not env.exists():
        return ""
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("ADMIN_PASSWORD_HASH="):
            return line.split("=", 1)[1].strip()
    return ""


def main() -> int:
    from_env = env_hash()
    print(f"music.env declares a hash: {bool(from_env)}")

    new_hash = from_env or hash_password(OWNER_PASSWORD)
    created = False

    with SessionLocal() as db:
        user = db.execute(
            text(
                "select id, password_hash, is_active from app_users "
                "where username = :u for update"
            ),
            {"u": USERNAME},
        ).fetchone()

        if user is None:
            db.execute(
                text(
                    "insert into app_users (username, password_hash, is_active, "
                    "failed_login_attempts, created_at, updated_at) "
                    "values (:u, :h, true, 0, now(), now())"
                ),
                {"u": USERNAME, "h": new_hash},
            )
            created = True
            print("Admin account created from music.env.")
        elif not verify_password(OWNER_PASSWORD, user.password_hash or ""):
            db.execute(
                text(
                    "update app_users set password_hash = :h, is_active = true, "
                    "failed_login_attempts = 0, locked_until = null, "
                    "token_version = coalesce(token_version, 0) + 1, updated_at = now() "
                    "where username = :u"
                ),
                {"u": USERNAME, "h": new_hash},
            )
            print("Password did not match — reset from music.env, sessions revoked.")
        else:
            print(f"Already correct: password {OWNER_PASSWORD!r} works. No change.")

        # Keep the one-shot marker in step with the row we just wrote.
        marker = db.execute(
            text("select value from app_settings where key = :k"), {"k": APPLIED_KEY}
        ).scalar_one_or_none()
        if marker is None:
            db.execute(
                text("insert into app_settings (key, value) values (:k, :v)"),
                {"k": APPLIED_KEY, "v": new_hash},
            )
        elif marker != new_hash:
            db.execute(
                text("update app_settings set value = :v, updated_at = now() where key = :k"),
                {"k": APPLIED_KEY, "v": new_hash},
            )
        db.commit()

    with SessionLocal() as db:
        row = db.execute(
            text("select password_hash, is_active from app_users where username = :u"),
            {"u": USERNAME},
        ).fetchone()
        ok = verify_password(OWNER_PASSWORD, row.password_hash if row else "")
        active = bool(row and row.is_active)
        print(f"VERIFY: {OWNER_PASSWORD!r} accepted = {ok}; account active = {active}")
        if created:
            print("No other change was made to the database.")
        return 0 if ok and active else 1


if __name__ == "__main__":
    raise SystemExit(main())