"""
T-Music TG Credential Sealing CLI
Encrypts plaintext Telegram credentials (session_string, api_hash) stored in
the tg_accounts table, in place, using Fernet keyed by SESSION_SECRET.

Usage:
  python scripts/secure_tg_accounts.py            # apply encryption
  python scripts/secure_tg_accounts.py --check    # report only, change nothing

The application also self-heals on startup (app/tg/manager.py), but this
script allows sealing the database without starting the full stack.
"""
import argparse
import sys
from pathlib import Path

# Add tmusic backend to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
TMUSIC_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = TMUSIC_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.vfs import TGAccount  # noqa: E402
from app.tg.manager import encrypt_session_string, is_encrypted  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Encrypt TG credentials at rest.")
    parser.add_argument("--check", action="store_true", help="Report plaintext rows only.")
    args = parser.parse_args()

    if not settings.SESSION_SECRET:
        print("ERROR: SESSION_SECRET is not set in music.env — cannot encrypt.", file=sys.stderr)
        return 2

    plaintext_rows = 0
    sealed_fields = 0

    with SessionLocal() as db:
        accounts = db.scalars(select(TGAccount)).all()
        for acc in accounts:
            row_plain = False
            if acc.session_string and not is_encrypted(acc.session_string, settings.SESSION_SECRET):
                row_plain = True
                if not args.check:
                    acc.session_string = encrypt_session_string(acc.session_string, settings.SESSION_SECRET)
                sealed_fields += 1
            if acc.api_hash and not is_encrypted(acc.api_hash, settings.SESSION_SECRET):
                row_plain = True
                if not args.check:
                    acc.api_hash = encrypt_session_string(acc.api_hash, settings.SESSION_SECRET)
                sealed_fields += 1
            if row_plain:
                plaintext_rows += 1
                print(f"  account id={acc.id} name={acc.name!r}: plaintext credentials found")

        if not args.check and sealed_fields:
            db.commit()

    if args.check:
        print(f"Check complete: {plaintext_rows} account(s) with plaintext credentials.")
    else:
        print(f"Sealing complete: {sealed_fields} field(s) encrypted across {plaintext_rows} account(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
