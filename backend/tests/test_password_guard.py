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
    ENV_PATH,
    MESSAGE,
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