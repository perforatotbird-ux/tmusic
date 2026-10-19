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


def test_nested_compilation_and_artist_breadcrumbs():
    """Verify nested catalog structure: compilation -> sub-compilation -> artist, with breadcrumbs."""
    db = SessionLocal()
    try:
        from app.models.music import MusicArtist
        from sqlalchemy import select

        cab314 = db.execute(
            select(MusicArtist).where(MusicArtist.folder_id == 15310)
        ).scalars().first()
        if not cab314:
            return  # skip if folder not in current test DB

        assert cab314.kind == "compilation"
        assert cab314.parent_id is not None

        detail_314 = library_service.get_artist_detail(db, cab314.id)
        assert len(detail_314.children) > 0
        assert len(detail_314.breadcrumbs) >= 1
        assert detail_314.breadcrumbs[0].display_name == "Пранки"

        # Check that parent compilation (Пранки) has 314 as a child with children_count > 0
        detail_pranki = library_service.get_artist_detail(db, cab314.parent_id)
        child_314 = next((c for c in detail_pranki.children if c.id == cab314.id), None)
        assert child_314 is not None
        assert child_314.children_count > 0

        # Check leaf child inside 314
        first_child = detail_314.children[0]
        assert first_child.children_count == 0
        assert first_child.album_count > 0
        child_detail = library_service.get_artist_detail(db, first_child.id)
        assert len(child_detail.breadcrumbs) >= 2
        assert child_detail.breadcrumbs[0].display_name == "Пранки"
        assert child_detail.breadcrumbs[1].id == cab314.id
        assert len(child_detail.albums) > 0
    finally:
        db.close()


def test_root_artist_filtering():
    """Verify that root listing excludes nested artists, while search can find them."""
    db = SessionLocal()
    try:
        from app.models.music import MusicArtist
        from sqlalchemy import select

        nested_artists = db.execute(
            select(MusicArtist).where(MusicArtist.parent_id.is_not(None))
        ).scalars().all()
        if not nested_artists:
            return

        # Listing with root_only=True must not contain any nested artists
        root_cards = library_service.list_artists(db, root_only=True, limit=500)
        for card in root_cards:
            assert card.parent_id is None, f"Found nested artist {card.display_name} in root listing"

        # Search for a known nested artist should find it
        sample = nested_artists[0]
        search_cards = library_service.list_artists(db, search=sample.display_name, limit=50)
        matching_ids = [c.id for c in search_cards]
        assert sample.id in matching_ids, f"Search failed to find nested artist {sample.display_name}"
    finally:
        db.close()

