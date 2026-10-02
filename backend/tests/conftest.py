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


@pytest.fixture(autouse=True)
def isolate_external_disk_cache(tmp_path, monkeypatch):
    """Point the disk-cache singleton at a per-test empty directory."""
    monkeypatch.setattr(external_cache, "root", tmp_path / "external")
    return tmp_path / "external"
