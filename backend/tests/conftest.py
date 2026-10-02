"""
Shared test fixtures.

The external provider disk cache (lyrics / last.fm) persists between calls and
across runs. Every test must see a cold cache and must not write into the real
`cache/external/` directory, or negative entries cached by one test leak into
the next and network stubs silently never fire.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.core.disk_cache import external_cache  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from password_guard import (  # noqa: E402
    check_env_unchanged,
    snapshot_admin_hash_line,
)


@pytest.fixture(autouse=True)
def isolate_external_disk_cache(tmp_path, monkeypatch):
    """Point the disk-cache singleton at a per-test empty directory."""
    monkeypatch.setattr(external_cache, "root", tmp_path / "external")
    return tmp_path / "external"


@pytest.fixture(autouse=True)
def protect_admin_password():
    """Owner decision 2026-10-02: the panel password is always "123".

    ADMIN_PASSWORD_HASH in music.env is the single source of truth. A test that
    rewrites it silently changes the real panel password, so every test is
    bracketed by a snapshot of that line and the suite FAILS if it changed.
    """
    before = snapshot_admin_hash_line()
    yield
    check_env_unchanged(before)
