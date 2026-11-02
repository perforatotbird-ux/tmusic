"""Regression: library search must ignore JS-null query strings."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings
from app.core.database import SessionLocal
from app.repositories.artist_repository import ArtistRepository
from app.repositories.album_repository import AlbumRepository

pytestmark = pytest.mark.skipif(
    bool(settings.TEST_DATABASE_SCHEMA),
    reason="requires migrated catalog data; skipped in an isolated test schema",
)


def test_artist_repo_ignores_null_search():
    with SessionLocal() as db:
        with_null = ArtistRepository.list_artists(db, kind="artist", search="null", limit=50)
        without = ArtistRepository.list_artists(db, kind="artist", search=None, limit=50)
        assert len(with_null) == len(without)
        assert len(without) > 0


def test_album_repo_ignores_null_search():
    with SessionLocal() as db:
        with_null = AlbumRepository.list_albums(db, search="null", limit=20)
        without = AlbumRepository.list_albums(db, search=None, limit=20)
        assert len(with_null) == len(without)
        assert len(without) > 0
