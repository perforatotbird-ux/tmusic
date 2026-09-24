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
from app.repositories.album_repository import AlbumRepository
from app.repositories.artist_repository import ArtistRepository
from app.services import lastfm_service
from app.services.music_library_service import CatalogAdminService, library_service
from app.api.admin_catalog_router import catalog_scan_artist

client = TestClient(app)


def test_catalog_endpoints_require_admin():
    for method, path in (
        ("get", "/api/admin/catalog/artists"),
        ("get", "/api/admin/catalog/albums"),
        ("patch", "/api/admin/catalog/artists/1"),
        ("post", "/api/admin/catalog/artists/1/scan"),
        ("post", "/api/admin/catalog/albums/1/scan"),
        ("post", "/api/admin/catalog/artists"),
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
    db = SessionLocal()
    artist = None
    try:
        artist = CatalogAdminService.create_collection(db, f"T_SCAN_{uuid.uuid4().hex[:8]}", "artist")
        monkeypatch.setattr(lastfm_service, "metadata_ready", lambda: True)
        monkeypatch.setattr(lastfm_service, "get_artist_info", lambda name: {
            "summary": "Updated biography",
            "mbid": "scan-mbid",
            "image_url": "https://example.test/cover.jpg",
        })
        result = asyncio.run(catalog_scan_artist(
            artist.id,
            db=db,
            user=SimpleNamespace(id=1, username="admin"),
        ))
        db.refresh(artist)
        assert result["updated"] is True
        assert artist.bio == "Updated biography"
        assert artist.mbid == "scan-mbid"
        assert artist.enriched_at is not None
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
        cards = {c.id: c for c in library_service.list_artists(db, limit=500)}
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
