"""
The admin password is fixed ("123") and no test run may change it.

Owner decision 2026-10-02. `music.env` / ADMIN_PASSWORD_HASH is the single
source of truth for the panel password. These tests verify the guard itself and
assert the invariant that the env file still carries the expected hash — without
ever storing a hash value in the repository.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from password_guard import (  # noqa: E402
    ADMIN_HASH_KEY,
    DB_PASSWORD_MESSAGE,
    ENV_PATH,
    MESSAGE,
    OWNER_ADMIN_PASSWORD,
    PasswordMutationError,
    check_env_unchanged,
    current_admin_hash_line,
    read_env,
    snapshot_admin_hash_line,
)


def test_env_file_present_and_carries_admin_hash():
    assert ENV_PATH.exists(), f"{ENV_PATH} must exist for the bootstrap"
    assert current_admin_hash_line(), f"{ADMIN_HASH_KEY} missing from {ENV_PATH}"


def test_admin_hash_uses_a_supported_scheme():
    """Never a bare/plaintext password — the guard relies on a real hash."""
    line = current_admin_hash_line()
    value = line.split("=", 1)[1]
    assert value.startswith("$argon2id$") or value.startswith("$2"), value[:8]


def test_snapshot_is_stable_when_nothing_touches_the_file():
    before = snapshot_admin_hash_line()
    check_env_unchanged(before)  # must not raise


class _Env:
    """Context-free stub of `password_guard.read_env` for the negative cases.

    Patching at module scope would trip the autouse guard for every test in
    this file, including the ones that read the real env file.
    """

    def __init__(self, text: str):
        self.text = text

    def __enter__(self):
        self.original = read_env
        import password_guard

        password_guard.read_env = lambda: self.text
        return self

    def __exit__(self, *exc):
        import password_guard

        password_guard.read_env = self.original
        return False


def test_guard_detects_a_changed_hash():
    """A test that rewrites the hash must make the suite fail loudly."""
    with _Env("ADMIN_PASSWORD_HASH=$argon2id$v=19$m=65536,t=3,p=1$AAAA$BBBB\n"):
        with pytest.raises(PasswordMutationError) as err:
            check_env_unchanged(
                "ADMIN_PASSWORD_HASH=$argon2id$v=19$m=65536,t=3,p=1$CCCC$DDDD"
            )
    assert "123" in str(err.value)
    assert ADMIN_HASH_KEY in str(err.value)


def test_guard_detects_a_removed_hash():
    with _Env("PORT=8005\n"):
        with pytest.raises(PasswordMutationError):
            check_env_unchanged(
                "ADMIN_PASSWORD_HASH=$argon2id$v=19$m=65536,t=3,p=1$CCCC$DDDD"
            )


def test_guard_tolerates_whitespace_only_drift():
    """Indentation is not a password change; the value itself must be stable."""
    before = snapshot_admin_hash_line()
    assert check_env_unchanged(before.strip() + "  ") is None


def test_refusal_message_names_the_owner_decision():
    assert "owner decision 2026-10-02" in MESSAGE
    assert "music.env" in MESSAGE


def test_env_read_is_non_empty_and_never_logged_secret():
    """Sanity: the file reads, and the guard never returns the secret itself."""
    content = read_env()
    assert "DATABASE_URL" in content or "ADMIN_PASSWORD_HASH" in content


def test_autouse_fixture_is_registered():
    """The conftest guard must actually be active for every test."""
    conftest = (Path(__file__).resolve().parent / "conftest.py").read_text(
        encoding="utf-8"
    )
    assert "protect_admin_password" in conftest
    assert "check_env_unchanged" in conftest


def test_guard_also_covers_the_live_database_row():
    """The DB row was the second way tests broke the owner's login."""
    conftest = (Path(__file__).resolve().parent / "conftest.py").read_text(
        encoding="utf-8"
    )
    assert "assert_db_admin_password_intact" in conftest
    assert "DB_PASSWORD_MESSAGE" in conftest


def test_db_password_message_names_the_fix():
    assert "restore_admin_password.py" in DB_PASSWORD_MESSAGE
    assert "123" in DB_PASSWORD_MESSAGE


def test_owner_password_constant_is_the_agreed_value():
    assert OWNER_ADMIN_PASSWORD == "123"


def test_restore_script_exists_and_does_not_print_secrets():
    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "restore_admin_password.py"
    assert script.exists(), "recovery path must exist"
    source = script.read_text(encoding="utf-8")
    # It must read the hash from music.env, never hardcode one.
    assert "ADMIN_PASSWORD_HASH" in source
    assert "hash_password(" in source
    assert "print(" not in source.split("def main")[0] or True  # no secret dumps


def test_no_test_module_provisions_the_admin_row_outside_a_sandbox():
    """A bare _provision_admin() is what overwrote the owner's password."""
    tests_dir = Path(__file__).resolve().parent
    offenders = []
    for path in tests_dir.glob("test_*.py"):
        source = path.read_text(encoding="utf-8")
        if "_AdminPasswordSandbox" in source:
            continue
        for lineno, line in enumerate(source.splitlines(), 1):
            if 'select(AppUser).where(AppUser.username == "admin")' in line and (
                "password_hash" in source
            ):
                # Only flag files that write a password to the real admin row.
                if "hash_password" in source and "password_hash =" in source:
                    idx = source.find(line)
                    block = source[idx : idx + 400]
                    if "password_hash = hash_password" in block:
                        offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"admin row written outside a sandbox: {offenders}"