"""
Guard: the admin password is fixed and must never be mutated by a test run.

Owner decision 2026-10-02: the panel password is always "123", and
`music.env` / `ADMIN_PASSWORD_HASH` is the single source of truth for it.

Historically a test run could overwrite that line, which silently changed the
panel password. These helpers fail the suite loudly instead of tolerating the
edit silently.

No hash value is stored in this module; it only compares the line that is
already on disk.
"""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = PROJECT_ROOT / "music.env"

ADMIN_HASH_KEY = "ADMIN_PASSWORD_HASH"
_ADMIN_HASH_LINE = re.compile(r"^[ \t]*ADMIN_PASSWORD_HASH[ \t]*=.*$", re.MULTILINE)

MESSAGE = (
    'REFUSED: the admin password must stay "123" (owner decision 2026-10-02). '
    "Tests may not modify ADMIN_PASSWORD_HASH in music.env, nor the "
    "app_settings.admin_bootstrap_hash row. The bootstrap password is managed "
    "in music.env only - see obsidian/05-Инфраструктура/music.env.md."
)


class PasswordMutationError(RuntimeError):
    """Raised when a test run tries to change the fixed admin password."""


def read_env() -> str:
    if not ENV_PATH.exists():
        return ""
    return ENV_PATH.read_text(encoding="utf-8")


def current_admin_hash_line() -> str:
    match = _ADMIN_HASH_LINE.search(read_env())
    return match.group(0).strip() if match else ""


def snapshot_admin_hash_line() -> str:
    """Capture the current line so a later edit can be detected."""
    return current_admin_hash_line()


def assert_admin_hash_intact(expected_line: str) -> None:
    """Fail if the env file no longer carries `expected_line` verbatim."""
    actual = current_admin_hash_line()
    if not actual:
        raise PasswordMutationError(
            f"{MESSAGE}\nReason: {ADMIN_HASH_KEY} is missing from {ENV_PATH}."
        )
    if actual != expected_line.strip():
        raise PasswordMutationError(
            f"{MESSAGE}\nReason: {ADMIN_HASH_KEY} was altered on disk."
        )


def check_env_unchanged(before: str) -> None:
    """Teardown hook: verify the hash line survived the test run unchanged."""
    if not before:
        raise PasswordMutationError(
            f"{MESSAGE}\nReason: {ADMIN_HASH_KEY} is missing from {ENV_PATH}."
        )
    assert_admin_hash_intact(before)


def assert_bootstrap_setting_untouched(db_value: str, expected_line: str) -> None:
    """Guard the DB-side mirror of the applied bootstrap hash."""
    if db_value and db_value.strip() != _hash_from_line(expected_line):
        raise PasswordMutationError(
            f"{MESSAGE}\nReason: app_settings.admin_bootstrap_hash diverged from "
            "ADMIN_PASSWORD_HASH in music.env."
        )


def _hash_from_line(line: str) -> str:
    return line.split("=", 1)[1].strip() if "=" in line else ""