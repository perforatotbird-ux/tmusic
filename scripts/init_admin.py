"""
T-Music Admin Provisioning CLI
Sets the admin panel password (bcrypt).

Usage:
  python scripts/init_admin.py                      # prompt for password interactively
  python scripts/init_admin.py --password "s3cret"  # non-interactive
  python scripts/init_admin.py --hash-only "s3cret" # print bcrypt hash for ADMIN_PASSWORD_HASH

Replaces the historical default-credentials flow (admin/admin123, PIN 1234),
which was removed in the 2026-09 security audit (finding C-2).
"""
import argparse
import getpass
import sys
from pathlib import Path

# Add tmusic backend to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
TMUSIC_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = TMUSIC_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.auth import AppUser  # noqa: E402

ADMIN_USERNAME = "admin"


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision the T-Music admin account.")
    parser.add_argument("--password", help="New admin password (min 8 chars).")
    parser.add_argument("--hash-only", metavar="PASSWORD",
                        help="Only print the bcrypt hash (for ADMIN_PASSWORD_HASH in music.env).")
    args = parser.parse_args()

    if args.hash_only is not None:
        if len(args.hash_only) < 8:
            print("ERROR: password must be at least 8 characters.", file=sys.stderr)
            return 2
        print(hash_password(args.hash_only))
        return 0

    password = args.password
    if password is None:
        password = getpass.getpass("New admin password (min 8 chars): ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("ERROR: passwords do not match.", file=sys.stderr)
            return 2

    if password is not None and len(password) < 8:
        print("ERROR: password must be at least 8 characters.", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        user = db.execute(
            select(AppUser).where(AppUser.username == ADMIN_USERNAME)
        ).scalar_one_or_none()

        if user is None:
            if password is None:
                print("ERROR: admin account does not exist; a password is required to create it.",
                      file=sys.stderr)
                return 2
            user = AppUser(username=ADMIN_USERNAME, password_hash=hash_password(password),
                           is_active=True)
            db.add(user)
            print("Admin account created.")
        else:
            if password is not None:
                user.password_hash = hash_password(password)
                user.failed_login_attempts = 0
                user.locked_until = None
                print("Admin password updated (bcrypt).")
            if not user.is_active:
                user.is_active = True
                print("Admin account re-activated.")

        db.commit()

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
