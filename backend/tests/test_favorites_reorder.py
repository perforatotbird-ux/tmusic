"""
Unit tests for favorites toggle + playlist reorder (in-memory SQLite, no network).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.auth import AppUser
from app.models.base import Base
from app.models.playlist import Playlist, PlaylistItem
from app.models.playback import UserFavorite
from app.models.vfs import Folder, VFile
from app.services.playback_service import playback_service
from app.services.playlist_service import playlist_service


def _make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            AppUser.__table__,
            Folder.__table__,
            VFile.__table__,
            Playlist.__table__,
            PlaylistItem.__table__,
            UserFavorite.__table__,
        ],
    )
    Session = sessionmaker(bind=engine)
    db = Session()
    user = AppUser(username="tester", password_hash="x", is_active=True)
    db.add(user)
    db.commit()
    folder = Folder(name="Album")
    db.add(folder)
    db.commit()
    files = []
    for i in range(3):
        vf = VFile(
            name=f"track-{i}.mp3",
            mime_type="audio/mpeg",
            folder_id=folder.id,
            is_trash=False,
        )
        db.add(vf)
        files.append(vf)
    db.commit()
    return db, user, files


def test_favorite_toggle_and_list():
    db, user, files = _make_session()
    try:
        assert playback_service.is_favorite(db, user.id, files[0].id) is False
        assert playback_service.toggle_favorite(db, user.id, files[0].id) is True
        assert playback_service.is_favorite(db, user.id, files[0].id) is True
        assert playback_service.toggle_favorite(db, user.id, files[0].id) is False
        assert playback_service.is_favorite(db, user.id, files[0].id) is False

        playback_service.toggle_favorite(db, user.id, files[1].id)
        rows, total = playback_service.list_favorites(db, user.id)
        assert total == 1
        assert rows[0][0].file_id == files[1].id
        assert rows[0][1].name == "track-1.mp3"
    finally:
        db.close()


def test_playlist_reorder_permutation():
    db, user, files = _make_session()
    try:
        pl = playlist_service.create_playlist(db, user.id, "Order me")
        items = [playlist_service.add_track(db, user.id, pl.id, f.id) for f in files]
        original = [i.id for i in items]
        assert original != list(reversed(original)) or len(set(original)) == 3

        reversed_ids = list(reversed(original))
        assert playlist_service.reorder_items(db, user.id, pl.id, reversed_ids) is True
        rows = playlist_service.playlist_tracks(db, pl.id)
        assert [r[0].id for r in rows] == reversed_ids

        # Wrong permutation (duplicate + foreign id) must be rejected
        bad = [original[0], original[0], 999999]
        assert playlist_service.reorder_items(db, user.id, pl.id, bad) is False
        # Unknown playlist
        assert playlist_service.reorder_items(db, user.id, 424242, original) is False
    finally:
        db.close()
