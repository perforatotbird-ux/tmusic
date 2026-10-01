"""Create the optional Compose PostgreSQL password file without printing it.

Run before initializing a NEW db volume. This does not rotate a database role
or overwrite an existing secret. No password is accepted on the command line.
"""
import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parent.parent


def create_secret(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    password = secrets.token_urlsafe(32)
    # Exclusive create avoids accidental rotation and follows no existing link.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(password + '\n')


def main() -> int:
    path = ROOT / '.secrets' / 'postgres_password'
    try:
        create_secret(path)
    except FileExistsError:
        print('Secret already exists; not overwritten. See the PostgreSQL rotation runbook.', file=sys.stderr)
        return 1
    except OSError:
        print('Could not create secret file. Check directory permissions.', file=sys.stderr)
        return 1
    print('Created .secrets/postgres_password. Value is not printed.')
    print('Configure DATABASE_URL in your private music.env before starting the db profile.')
    print('An existing PostgreSQL volume needs a separate database-role password rotation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
