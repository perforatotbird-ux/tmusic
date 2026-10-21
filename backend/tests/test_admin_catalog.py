"""Admin catalog curation tests (rename / collections / moves / cover gate)."""
import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import SessionLocal
from app.models import vfs
from app.models.music import MusicAlbum, MusicArtist
from app.repositories.album_repository import AlbumRepository
from app.repositories.artist_repository import ArtistRepository
from app.services import lastfm_service
from app.services.music_library_service import CatalogAdminService, library_service
from app.services import scan_service
from app.services.scan_service import ScanBusy, ScanMissing, ScanUnavailable

client = TestClient(app)

# Two distinct "real" artwork URLs plus the shared asset Last.fm serves for
# every artist that has no picture of its own.
REAL_ART = "https://lastfm.freetls.fastly.net/i/u/300x300/nevermind.png"
OTHER_ART = "https://lastfm.freetls.fastly.net/i/u/300x300/bleach.png"
PLACEHOLDER_ART = (
    "https://lastfm.freetls.fastly.net/i/u/300x300/2a96cbd8b46e442fc41c2b86b821562f.png"
)


def _stub_cover_cache(monkeypatch, warmed):
    """Record cache warms instead of hitting the network during a scan."""
    from app.services import cover_cache_service

    async def _warm(url):
        warmed.append(url)
        return None, None

    monkeypatch.setattr(cover_cache_service.cover_cache, "fetch_and_cache", _warm)


def _make_artist(db, kind="artist"):
    return CatalogAdminService.create_collection(db, f"T_SCAN_{uuid.uuid4().hex[:8]}", kind)


def _make_album(db, artist, title="Nevermind"):
    folder = vfs.Folder(name=f"T_SCAN_F_{uuid.uuid4().hex[:8]}")
    db.add(folder)
    db.flush()
    album = MusicAlbum(folder_id=folder.id, artist_id=artist.id, title=title)
    db.add(album)
    db.commit()
    db.refresh(album)
    return album


def run_scan(artist, db):
    """Run the scan through the service and collect the progress trail.

    The old coroutine was awaited inline; now the work lives in
    `scan_service.scan_artist_in_db` and reports progress through a callback —
    the same path the background job uses.
    """
    stages = []

    def report(stage, message, current=None, total=None):
        stages.append((stage, current, total))

    result = asyncio.run(scan_service.scan_artist_in_db(db, artist.id, report))
    result["_stages"] = stages
    return result


def test_catalog_endpoints_require_admin():
    for method, path in (
        ("get", "/api/admin/catalog/artists"),
        ("get", "/api/admin/catalog/albums"),
        ("patch", "/api/admin/catalog/artists/1"),
        ("post", "/api/admin/catalog/artists/1/scan"),
        ("post", "/api/admin/catalog/albums/1/scan"),
        ("post", "/api/admin/catalog/artists"),
        ("post", "/api/admin/catalog/artists/batch-delete"),
        ("delete", "/api/admin/catalog/artists/1"),
        ("patch", "/api/admin/catalog/albums/1"),
    ):
        if method in ("patch", "post"):
            resp = getattr(client, method)(path, json={})
        else:
            resp = getattr(client, method)(path)
        assert resp.status_code in (401, 403), f"{method.upper()} {path} -> {resp.status_code}"


def test_cover_mutation_requires_admin():
    # Catalog artwork is admin-only since catalog curation was introduced.
    resp = client.post("/api/library/cover", data={"target_type": "artist", "target_id": 1})
    assert resp.status_code in (401, 403), f"POST cover -> {resp.status_code}"
    resp = client.delete("/api/library/cover?target_type=artist&target_id=1")
    assert resp.status_code in (401, 403), f"DELETE cover -> {resp.status_code}"


def test_collection_lifecycle_rename_delete():
    db = SessionLocal()
    name = f"TEST_TMP_{uuid.uuid4().hex[:8]}"
    artist = None
    try:
        artist = CatalogAdminService.create_collection(db, name, "genre_collection")
        assert artist.id and artist.display_name == name
        assert artist.kind == "genre_collection"
        assert artist.locked is True

        renamed = CatalogAdminService.update_artist(db, artist.id, display_name=name + "_renamed")
        assert renamed.display_name == name + "_renamed"
        assert renamed.search_name == (name + "_renamed").lower()
        assert renamed.locked is True

        changed = CatalogAdminService.update_artist(db, artist.id, kind="compilation")
        assert changed.kind == "compilation"

        with pytest.raises(HTTPException) as exc:
            CatalogAdminService.update_artist(db, artist.id, kind="nope")
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            CatalogAdminService.update_artist(db, artist.id, display_name="   ")
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            CatalogAdminService.create_collection(db, "   ")
        assert exc.value.status_code == 400

        result = CatalogAdminService.delete_artist(db, artist.id)
        assert result["ok"] is True
        artist = None
        assert ArtistRepository.get_by_id(db, renamed.id) is None
    finally:
        try:
            if artist is not None:
                CatalogAdminService.delete_artist(db, artist.id)
        except Exception:
            pass
        db.close()


def test_admin_scan_artist_refreshes_metadata(monkeypatch):
    """A scan must write the fresh biography, MBID and cover — and say so."""
    db = SessionLocal()
    artist = None
    warmed = []
    try:
        artist = _make_artist(db)
        monkeypatch.setattr(lastfm_service, "metadata_ready", lambda: True)
        # The provider getter is called with force=True by the scan, so the
        # stub has to accept the keyword — that is the whole point of the flag.
        monkeypatch.setattr(lastfm_service, "get_artist_info", lambda name, force=False: {
            "summary": "Updated biography",
            "mbid": "scan-mbid",
            "image_url": REAL_ART,
        })
        _stub_cover_cache(monkeypatch, warmed)

        result = run_scan(artist, db)
        db.refresh(artist)

        assert result["updated"] is True
        assert result["fields"] == ["bio", "mbid", "image_url"]
        assert result["image_source"] == "lastfm"
        assert result["albums_scanned"] == 0
        assert result["albums_updated"] == 0

        assert artist.bio == "Updated biography"
        assert artist.mbid == "scan-mbid"
        assert artist.image_url == REAL_ART
        assert artist.enriched_at is not None
        # Cards resolve covers through the local disk cache, so the chosen picture
        # must have been warmed once or the card would 404 on display.
        assert warmed == [REAL_ART]
    finally:
        try:
            if artist is not None:
                CatalogAdminService.delete_artist(db, artist.id)
        except Exception:
            pass
        db.close()


def test_admin_scan_artist_takes_cover_from_album_when_provider_has_none(monkeypatch):
    """artist.getInfo has no real artwork → the album cover becomes the artist's.

    This is the regression guard for the bug where the shared placeholder asset
    was written into every scanned artist, collapsing them onto one picture.
    """
    db = SessionLocal()
    artist = album = None
    warmed = []
    try:
        artist = _make_artist(db)
        album = _make_album(db, artist)
        monkeypatch.setattr(lastfm_service, "metadata_ready", lambda: True)
        monkeypatch.setattr(lastfm_service, "get_artist_info", lambda name, force=False: {
            # `clean_bio` already stripped the «Read more on Last.fm» stub and
            # `pick_image` already dropped the placeholder before this point.
            "summary": "",
            "mbid": None,
            "image_url": None,
        })
        monkeypatch.setattr(
            lastfm_service, "get_album_info",
            lambda name, title, force=False: {"image_url": REAL_ART},
        )
        _stub_cover_cache(monkeypatch, warmed)

        result = run_scan(artist, db)
        db.refresh(artist)
        db.refresh(album)

        assert result["albums_scanned"] == 1
        assert result["albums_updated"] == 1
        assert album.cover_url == REAL_ART
        assert result["image_source"] == "album"
        assert artist.image_url == REAL_ART
        assert PLACEHOLDER_ART not in (artist.image_url or "")
        # No biography is better than the stub.
        assert not (artist.bio or "").strip()
        assert warmed == [REAL_ART]
    finally:
        try:
            if album is not None:
                folder_id = album.folder_id
                db.delete(album)
                db.flush()
                folder = db.get(vfs.Folder, folder_id)
                if folder is not None:
                    db.delete(folder)
                db.commit()
        except Exception:
            db.rollback()
        try:
            if artist is not None:
                CatalogAdminService.delete_artist(db, artist.id)
        except Exception:
            pass
        db.close()


def test_admin_scan_artist_never_downgrades_existing_cover_and_is_idempotent(monkeypatch):
    """A hand-picked/real cover survives, and a second scan reports no change."""
    db = SessionLocal()
    artist = None
    warmed = []
    try:
        artist = _make_artist(db)
        artist.bio = "Existing biography"
        artist.mbid = "existing-mbid"
        artist.image_url = REAL_ART
        db.commit()
        db.refresh(artist)

        monkeypatch.setattr(lastfm_service, "metadata_ready", lambda: True)
        monkeypatch.setattr(lastfm_service, "get_artist_info", lambda name, force=False: {
            "summary": "Existing biography",
            "mbid": "existing-mbid",
            # A different (still real) provider image: it must NOT win over the
            # one already on the row.
            "image_url": OTHER_ART,
        })
        _stub_cover_cache(monkeypatch, warmed)

        result = run_scan(artist, db)
        db.refresh(artist)

        assert result["updated"] is False
        assert result["fields"] == []
        assert result["image_source"] == "existing"
        assert artist.image_url == REAL_ART
        assert warmed == []
    finally:
        try:
            if artist is not None:
                CatalogAdminService.delete_artist(db, artist.id)
        except Exception:
            pass
        db.close()


def test_admin_scan_artist_replaces_placeholder_cover(monkeypatch):
    """A placeholder already stored on the row is upgraded to real artwork."""
    db = SessionLocal()
    artist = None
    warmed = []
    try:
        artist = _make_artist(db)
        artist.image_url = PLACEHOLDER_ART
        db.commit()
        db.refresh(artist)

        monkeypatch.setattr(lastfm_service, "metadata_ready", lambda: True)
        monkeypatch.setattr(lastfm_service, "get_artist_info", lambda name, force=False: {
            "summary": "",
            "mbid": None,
            "image_url": REAL_ART,
        })
        _stub_cover_cache(monkeypatch, warmed)

        result = run_scan(artist, db)
        db.refresh(artist)

        assert result["updated"] is True
        assert result["fields"] == ["image_url"]
        assert result["image_source"] == "lastfm"
        assert artist.image_url == REAL_ART
        assert warmed == [REAL_ART]
    finally:
        try:
            if artist is not None:
                CatalogAdminService.delete_artist(db, artist.id)
        except Exception:
            pass
        db.close()


def test_admin_scan_artist_reports_lastfm_miss(monkeypatch):
    """Unknown artist on Last.fm → 404, not a silent no-op."""
    db = SessionLocal()
    artist = None
    try:
        artist = _make_artist(db)
        monkeypatch.setattr(lastfm_service, "metadata_ready", lambda: True)
        monkeypatch.setattr(lastfm_service, "get_artist_info", lambda name, force=False: None)

        with pytest.raises(ScanMissing) as exc:
            run_scan(artist, db)
        assert exc.value.status == 404
    finally:
        try:
            if artist is not None:
                CatalogAdminService.delete_artist(db, artist.id)
        except Exception:
            pass
        db.close()


def test_admin_scan_artist_requires_metadata_provider(monkeypatch):
    db = SessionLocal()
    artist = None
    try:
        artist = _make_artist(db)
        monkeypatch.setattr(lastfm_service, "metadata_ready", lambda: False)
        with pytest.raises(ScanUnavailable) as exc:
            run_scan(artist, db)
        assert exc.value.status == 503
    finally:
        try:
            if artist is not None:
                CatalogAdminService.delete_artist(db, artist.id)
        except Exception:
            pass
        db.close()


def test_delete_refuses_nonempty_collection():
    db = SessionLocal()
    try:
        artists = ArtistRepository.list_artists(db, limit=50)
        target = next((a for a, count, _, _ in artists if count), None)
        if target is None:
            pytest.skip("No artist with albums in test DB")
        with pytest.raises(HTTPException) as exc:
            CatalogAdminService.delete_artist(db, target.id)
        assert exc.value.status_code == 400
    finally:
        db.close()


def test_album_update_validation_no_mutation():
    db = SessionLocal()
    try:
        albums = AlbumRepository.list_albums(db, limit=1)
        if not albums:
            pytest.skip("No albums in test DB")
        album_id = albums[0].id

        with pytest.raises(HTTPException) as exc:
            CatalogAdminService.update_album(db, album_id, year=1700)
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            CatalogAdminService.update_album(db, album_id, title="  ")
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            CatalogAdminService.update_album(db, album_id, artist_id=999999999)
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            CatalogAdminService.update_album(db, 999999999, title="x")
        assert exc.value.status_code == 404
    finally:
        db.close()


def test_card_nesting_move_cycle_detach():
    """Card hierarchy: nest a card into another, reject cycles, detach on delete."""
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:6]
    parent = child = grandchild = None
    try:
        parent = CatalogAdminService.create_collection(db, f"T_NEST_P_{suffix}", "genre_collection")
        child = CatalogAdminService.create_collection(db, f"T_NEST_C_{suffix}", "artist")
        grandchild = CatalogAdminService.create_collection(db, f"T_NEST_G_{suffix}", "compilation")

        # Nest child into parent via update_artist(set_parent=True)
        moved = CatalogAdminService.update_artist(db, child.id, parent_id=parent.id, set_parent=True)
        assert moved.parent_id == parent.id

        # Nest grandchild into child → two-level chain
        CatalogAdminService.update_artist(db, grandchild.id, parent_id=child.id, set_parent=True)

        # Cards list exposes parent_name / children_count
        cards = {c.id: c for c in library_service.list_artists(db, search=f"_{suffix}", limit=100)}
        assert cards[child.id].parent_id == parent.id
        assert cards[child.id].parent_name == parent.display_name
        assert cards[parent.id].children_count >= 1

        # Self-nesting and descendant-nesting are rejected (cycle guard)
        with pytest.raises(HTTPException) as exc:
            CatalogAdminService.update_artist(db, parent.id, parent_id=parent.id, set_parent=True)
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            CatalogAdminService.update_artist(db, parent.id, parent_id=grandchild.id, set_parent=True)
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc:
            CatalogAdminService.update_artist(db, child.id, parent_id=999999999, set_parent=True)
        assert exc.value.status_code == 404

        # Create with a parent id attaches immediately
        leaf = CatalogAdminService.create_collection(db, f"T_NEST_L_{suffix}", "artist", parent_id=parent.id)
        assert leaf.parent_id == parent.id
        CatalogAdminService.delete_artist(db, leaf.id)

        # Explicit null detaches to root
        detached = CatalogAdminService.update_artist(db, grandchild.id, parent_id=None, set_parent=True)
        assert detached.parent_id is None

        # Deleting the parent detaches remaining children instead of cascading
        CatalogAdminService.update_artist(db, child.id, parent_id=parent.id, set_parent=True)
        result = CatalogAdminService.delete_artist(db, parent.id)
        assert result["ok"] is True
        assert result["detached_children"] >= 1
        parent = None
        still_there = ArtistRepository.get_by_id(db, child.id)
        assert still_there is not None and still_there.parent_id is None
        CatalogAdminService.delete_artist(db, child.id)
        child = None
        CatalogAdminService.delete_artist(db, grandchild.id)
        grandchild = None
    finally:
        for card in (parent, child, grandchild):
            try:
                if card is not None:
                    CatalogAdminService.delete_artist(db, card.id)
            except Exception:
                pass
        db.close()


def test_artist_detail_includes_children():
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:6]
    parent = child = None
    try:
        parent = CatalogAdminService.create_collection(db, f"T_DET_P_{suffix}", "genre_collection")
        child = CatalogAdminService.create_collection(db, f"T_DET_C_{suffix}", "artist", parent_id=parent.id)
        detail = library_service.get_artist_detail(db, parent.id)
        assert detail.parent_id is None
        assert any(c.id == child.id for c in detail.children)
        child_detail = library_service.get_artist_detail(db, child.id)
        assert child_detail.parent_id == parent.id
        assert child_detail.parent_name == parent.display_name
    finally:
        for card in (child, parent):
            try:
                if card is not None:
                    CatalogAdminService.delete_artist(db, card.id)
            except Exception:
                pass
        db.close()


def test_catalog_list_walks_one_level_at_a_time():
    """The admin list must not return every level at once.

    Regression: the nested sync stores album folders as catalog cards. The list
    asked for the whole catalog, so album cards came back as top-level rows and
    the admin read them as «Артист».
    """
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:6]
    parent = child = None
    try:
        parent = CatalogAdminService.create_collection(db, f"T_LVL_P_{suffix}", "artist")
        child = CatalogAdminService.create_collection(db, f"T_LVL_C_{suffix}", "standalone_album", parent_id=parent.id)

        roots = library_service.list_artists(db, search=f"T_LVL_P_{suffix}", root_only=True, limit=50)
        assert [c.id for c in roots] == [parent.id]

        # A child never shows up in the root level…
        assert all(c.id != child.id for c in library_service.list_artists(db, root_only=True, limit=500))

        # …only inside its parent, which is what the drill-down asks for.
        children = library_service.list_artists(db, parent_id=parent.id, limit=50)
        assert [c.id for c in children] == [child.id]
        assert children[0].parent_name == parent.display_name
    finally:
        for card in (child, parent):
            try:
                if card is not None:
                    CatalogAdminService.delete_artist(db, card.id)
            except Exception:
                pass
        db.close()


def test_sync_classifies_release_folders_as_releases():
    """A folder holding audio is a release, not «Артист»/«Сборник».

    Discography tree: root → «01 Albums» (grouping) → album (audio + Covers) and
    a leaf single (audio only).
    """
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:6]
    folders = []

    def _folder(name, parent=None):
        folder = vfs.Folder(name=f"{name}_{suffix}", parent_id=parent.id if parent else None)
        db.add(folder)
        db.flush()
        folders.append(folder)
        return folder

    def _file(folder, name, mime="audio/mpeg"):
        row = vfs.VFile(name=f"{name}.{mime.split('/')[-1]}", size=123, mime_type=mime, folder_id=folder.id, is_trash=False)
        db.add(row)
        db.flush()
        return row

    try:
        root = _folder("T_SYNC_ROOT")
        group = _folder("01 Albums", root)
        album = _folder("2001 - Album", group)
        _file(album, "track1")
        _file(album, "track2")
        covers = _folder("Covers", album)
        _file(covers, "front", "image/jpeg")
        single = _folder("2002 - Single", group)
        _file(single, "single")
        db.commit()

        CatalogAdminService.sync_catalog_folder_tree(db, root.id, catalog_kind="artist")

        cards = {
            c.folder_id: c
            for c in library_service.list_artists(db, search=f"_{suffix}", limit=200)
        }
        assert cards[root.id].kind == "artist"          # root keeps the chosen kind
        assert cards[group.id].kind == "genre_collection"  # grouping folder, not «Сборник»
        assert cards[album.id].kind == "standalone_album"  # has audio → release, Covers doesn't count
        assert cards[single.id].kind == "standalone_album"
        assert cards[album.id].parent_id == cards[group.id].id
        # A release card owns an album row for its folder; the grouping card does not.
        assert cards[album.id].album_count == 1
        assert cards[group.id].album_count == 0
    finally:
        try:
            db.rollback()
            from sqlalchemy import delete
            folder_ids = [f.id for f in folders]
            if folder_ids:
                # Raw deletes, deepest row set first: `delete_artist` refuses a
                # card that still has albums, which would leave test rows behind
                # in the live catalog.
                db.execute(delete(MusicAlbum).where(MusicAlbum.folder_id.in_(folder_ids)))
                db.execute(delete(MusicArtist).where(MusicArtist.folder_id.in_(folder_ids)))
                db.execute(delete(vfs.VFile).where(vfs.VFile.folder_id.in_(folder_ids)))
                # Children first, then parents — folders reference each other.
                for folder in reversed(folders):
                    db.execute(delete(vfs.Folder).where(vfs.Folder.id == folder.id))
                db.commit()
        except Exception:
            db.rollback()
        db.close()


def test_batch_delete_removes_cards_with_albums_and_detaches_children():
    """Bulk delete takes the card entirely: its albums go with it.

    The admin asked for «удалить целиком» — nothing blocks a card any more, and
    only an unknown id is reported in `skipped`. Nested cards are detached.
    """
    db = SessionLocal()
    suffix = uuid.uuid4().hex[:6]
    first = second = blocked = kid = None
    album = None
    try:
        first = CatalogAdminService.create_collection(db, f"T_BD_A_{suffix}", "genre_collection")
        second = CatalogAdminService.create_collection(db, f"T_BD_B_{suffix}", "genre_collection")
        blocked = CatalogAdminService.create_collection(db, f"T_BD_C_{suffix}", "artist")
        kid = CatalogAdminService.create_collection(db, f"T_BD_KID_{suffix}", "standalone_album", parent_id=first.id)
        # Album on the blocked card's own folder — `_make_album` would leave an
        # extra orphan folder behind in the live database.
        album = MusicAlbum(folder_id=blocked.folder_id, artist_id=blocked.id, title="Blocked")
        db.add(album)
        db.commit()
        db.refresh(album)

        result = CatalogAdminService.delete_artists(
            db, [first.id, second.id, blocked.id, 999999999]
        )

        assert result["deleted_count"] == 3
        assert set(result["deleted"]) == {first.id, second.id, blocked.id}
        # The card's album went with the card.
        assert result["deleted_albums"] == 1
        assert [item["code"] for item in result["skipped"]] == ["not_found"]

        assert ArtistRepository.get_by_id(db, blocked.id) is None
        assert AlbumRepository.get_by_id(db, album.id) is None
        # The nested card was detached, not deleted with its parent.
        detached = ArtistRepository.get_by_id(db, kid.id)
        assert detached is not None and detached.parent_id is None
        assert ArtistRepository.get_by_id(db, first.id) is None
        assert ArtistRepository.get_by_id(db, second.id) is None
    finally:
        try:
            db.rollback()
            from sqlalchemy import delete as _delete
            # Drop the album first — otherwise the card refuses to be deleted.
            if album is not None:
                db.execute(_delete(MusicAlbum).where(MusicAlbum.id == album.id))
                db.commit()
            for card in (kid, blocked):
                try:
                    if card is not None:
                        CatalogAdminService.delete_artist(db, card.id)
                except Exception:
                    db.rollback()
        except Exception:
            db.rollback()
        db.close()
