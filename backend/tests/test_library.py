"""
Tests for T-Music Library & Repositories
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import SessionLocal
from app.repositories.artist_repository import ArtistRepository
from app.repositories.album_repository import AlbumRepository
from app.repositories.file_repository import FileRepository
from app.services.music_library_service import library_service


def test_migrated_library_counts():
    db = SessionLocal()
    try:
        artists_count = ArtistRepository.count(db)
        albums_count = AlbumRepository.count(db)
        tracks_count = FileRepository.count_tracks(db)

        assert artists_count > 0, "Expected migrated artists in tgmusic"
        assert albums_count > 0, "Expected migrated albums in tgmusic"
        assert tracks_count > 0, "Expected migrated tracks in tgmusic"

        stats = library_service.get_stats(db)
        assert stats.artists_count == artists_count
        assert stats.albums_count == albums_count
        assert stats.tracks_count == tracks_count
    finally:
        db.close()


def test_artist_listing_and_detail():
    db = SessionLocal()
    try:
        artists = library_service.list_artists(db, limit=10)
        assert len(artists) > 0
        first_artist = artists[0]

        detail = library_service.get_artist_detail(db, first_artist.id)
        assert detail.id == first_artist.id
        assert detail.display_name == first_artist.display_name
        assert isinstance(detail.albums, list)
    finally:
        db.close()
