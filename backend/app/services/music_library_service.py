"""
T-Music Library Service
Coordinates artist catalog, discographies, custom cover art, and library statistics.

Cover URL contract (2026-10-03): artist/album cards NEVER expose remote URLs.
``_build_cover_url`` returns ``/api/library/cover?key=<cache-key>`` where the key
is ``cover_cache.url_to_key(remote_url)`` — the same deterministic key the disk
cache uses for ``{key}.bin``. The browser therefore always reads from the local
disk cache (``cache/music_images``); no per-request remote fetch happens on
display. Remote download happens only once — at enrichment time (see
``admin_catalog_router.catalog_scan_*`` / ``metadata_router.enrich_artist``,
which warm the cache via ``fetch_and_cache``) — or via the explicit admin
``POST /api/library/cover`` upload path. ``GET /cover?url=`` remains as a
backward-compatible explicit fetch, but cards must not emit it.
"""
from typing import Optional, List
from datetime import datetime
import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.music import MusicArtist, MusicAlbum
from app.models.vfs import VFile
from app.repositories.album_repository import AlbumRepository
from app.repositories.artist_repository import ArtistRepository
from app.repositories.catalog_repository import CatalogRepository
from app.repositories.file_repository import FileRepository
from app.repositories.folder_repository import FolderRepository
from app.schemas.library import (
    MusicArtistCard, MusicAlbumCard, MusicArtistDetail, LibraryStats, BreadcrumbItem
)
from app.services.cover_cache_service import cover_cache
from app.utils.time import utcnow

logger = logging.getLogger("tmusic.library")


class MusicLibraryService:
    """Business service for catalog navigation and metadata management."""

    @staticmethod
    def _build_custom_cover_url(key: Optional[str]) -> Optional[str]:
        if not key:
            return None
        return f"/api/library/cover?key={key}&custom=1"

    @staticmethod
    def _build_cover_url(url: Optional[str], custom_key: Optional[str], enriched_at: Optional[datetime]) -> Optional[str]:
        # Always serve from the local disk cache: derive the deterministic
        # cache key from the stored remote URL and point at ?key=.
        # enriched_at is kept in the signature for call-site compatibility.
        if custom_key:
            return None
        if not url:
            return None
        key = cover_cache.url_to_key(url)
        return f"/api/library/cover?key={key}"

    @classmethod
    def _cover_pair(cls, url: Optional[str], custom_key: Optional[str],
                    enriched_at: Optional[datetime]) -> tuple[Optional[str], Optional[str]]:
        """Returns (display_url, custom_url): custom cover wins over enriched URL."""
        custom_url = cls._build_custom_cover_url(custom_key)
        display_url = custom_url if custom_url else cls._build_cover_url(url, None, enriched_at)
        return display_url, custom_url

    @staticmethod
    def _album_sort_key(alb: MusicAlbum):
        year = alb.year
        return (year is None, year if year is not None else 0, alb.title or "")

    @classmethod
    def list_artists(
        cls,
        db: Session,
        kind: Optional[str] = None,
        search: Optional[str] = None,
        root_only: Optional[bool] = None,
        parent_id: Optional[int] = None,
        limit: int = 200,
        offset: int = 0
    ) -> List[MusicArtistCard]:
        artists = ArtistRepository.list_artists(
            db,
            kind=kind,
            search=search,
            root_only=root_only,
            parent_id=parent_id,
            limit=limit,
            offset=offset,
        )
        result = []
        for a, album_count, parent_name, children_count in artists:
            img_url, custom_url = cls._cover_pair(a.image_url, a.custom_image_key, a.enriched_at)
            result.append(MusicArtistCard(
                id=a.id,
                folder_id=a.folder_id,
                kind=a.kind,
                display_name=a.display_name,
                image_url=img_url,
                custom_image_url=custom_url,
                confidence=a.confidence,
                album_count=album_count or 0,
                parent_id=a.parent_id,
                parent_name=parent_name,
                children_count=children_count or 0,
                enriched_at=a.enriched_at
            ))
        return result

    @classmethod
    def list_albums(
        cls,
        db: Session,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        artist_id: Optional[int] = None,
    ) -> List[MusicAlbumCard]:
        if artist_id is not None:
            albums = AlbumRepository.list_by_artist_id(db, artist_id, limit=limit, offset=offset, search=search)
        else:
            albums = AlbumRepository.list_albums(db, search=search, limit=limit, offset=offset)
        result = []
        for alb in albums:
            cov_url, c_url = cls._cover_pair(alb.cover_url, alb.custom_image_key, alb.enriched_at)
            result.append(MusicAlbumCard(
                id=alb.id,
                folder_id=alb.folder_id,
                artist_id=alb.artist_id,
                title=alb.title,
                year=alb.year,
                mbid=alb.mbid,
                cover_url=cov_url,
                custom_image_url=c_url,
                confidence=alb.confidence,
                is_tracks=False,
                enriched_at=alb.enriched_at
            ))
        return result

    @classmethod
    def get_artist(cls, db: Session, artist_id: int) -> Optional[MusicArtist]:
        """Raw artist entity (repository access stays in the service layer)."""
        return ArtistRepository.get_by_id(db, artist_id)

    @classmethod
    def get_album(cls, db: Session, album_id: int) -> Optional[MusicAlbum]:
        return AlbumRepository.get_by_id(db, album_id)

    @classmethod
    def list_artist_albums(
        cls, db: Session, artist_id: int, limit: Optional[int] = None
    ) -> List[MusicAlbum]:
        """Album entities of one artist, ordered by year/title (admin scan pass)."""
        return AlbumRepository.list_by_artist_id(db, artist_id, limit=limit)

    @classmethod
    def count_artist_children(cls, db: Session, artist_id: int) -> int:
        return ArtistRepository.count_children(db, artist_id)

    @classmethod
    def first_album_track(cls, db: Session, folder_id: int) -> Optional[VFile]:
        """First playable audio track of an album folder (enrichment helper)."""
        return FileRepository.first_album_track(db, folder_id)

    @classmethod
    def get_artist_detail(cls, db: Session, artist_id: int) -> MusicArtistDetail:
        artist = ArtistRepository.get_by_id(db, artist_id)
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found.")

        # Albums already eager-loaded via selectinload — no second query.
        albums = sorted(artist.albums or [], key=cls._album_sort_key)
        album_cards = []
        for alb in albums:
            cov_url, c_url = cls._cover_pair(alb.cover_url, alb.custom_image_key, alb.enriched_at)
            is_tracks = alb.folder_id == artist.folder_id and artist.kind != "standalone_album"

            album_cards.append(MusicAlbumCard(
                id=alb.id,
                folder_id=alb.folder_id,
                artist_id=alb.artist_id,
                title=alb.title,
                year=alb.year,
                mbid=alb.mbid,
                cover_url=cov_url,
                custom_image_url=c_url,
                confidence=alb.confidence,
                is_tracks=is_tracks,
                enriched_at=alb.enriched_at
            ))

        art_img, custom_art_url = cls._cover_pair(artist.image_url, artist.custom_image_key, artist.enriched_at)

        child_cards = [
            MusicArtistCard(
                id=child.id,
                folder_id=child.folder_id,
                kind=child.kind,
                display_name=child.display_name,
                image_url=cls._cover_pair(child.image_url, child.custom_image_key, child.enriched_at)[0],
                custom_image_url=cls._build_custom_cover_url(child.custom_image_key),
                confidence=child.confidence,
                album_count=child_album_count,
                parent_id=artist.id,
                parent_name=child_parent_name or artist.display_name,
                children_count=child_children_count,
                enriched_at=child.enriched_at,
            )
            for child, child_album_count, child_parent_name, child_children_count in ArtistRepository.list_children(db, artist.id)
        ]

        parent_name = None
        if artist.parent_id:
            parent = ArtistRepository.get_by_id(db, artist.parent_id)
            parent_name = parent.display_name if parent else None

        ancestors = ArtistRepository.get_ancestors(db, artist.id)
        breadcrumbs = [
            BreadcrumbItem(id=anc.id, display_name=anc.display_name, kind=anc.kind)
            for anc in ancestors
        ]

        return MusicArtistDetail(
            id=artist.id,
            folder_id=artist.folder_id,
            kind=artist.kind,
            display_name=artist.display_name,
            search_name=artist.search_name,
            mbid=artist.mbid,
            image_url=art_img,
            custom_image_url=custom_art_url,
            bio=artist.bio,
            wikipedia_url=artist.wikipedia_url,
            confidence=artist.confidence,
            locked=artist.locked,
            parent_id=artist.parent_id,
            parent_name=parent_name,
            enriched_at=artist.enriched_at,
            albums=album_cards,
            children=child_cards,
            breadcrumbs=breadcrumbs,
        )

    @classmethod
    def get_stats(cls, db: Session) -> LibraryStats:
        """Single round-trip for all catalog aggregates."""
        row = CatalogRepository.stats(db)
        return LibraryStats(
            artists_count=row[0] or 0,
            albums_count=row[1] or 0,
            tracks_count=row[2] or 0,
            total_size_bytes=int(row[3] or 0)
        )

    @staticmethod
    def set_custom_cover(db: Session, target_type: str, target_id: int, key: Optional[str]):
        """Attach (or clear, with key=None) a custom cover on any catalog level.

        All three levels share one storage contract — a cover_cache key in
        ``custom_image_key`` — so the admin UI needs a single upload path.
        """
        if target_type == "artist":
            entity = ArtistRepository.get_by_id(db, target_id)
        elif target_type == "album":
            entity = AlbumRepository.get_by_id(db, target_id)
        elif target_type == "track":
            entity = FileRepository.get_by_id(db, target_id)
        else:
            raise HTTPException(
                status_code=400,
                detail="Invalid target_type. Must be 'artist', 'album' or 'track'.",
            )

        if not entity:
            raise HTTPException(status_code=404, detail=f"{target_type.capitalize()} not found.")

        entity.custom_image_key = key
        entity.updated_at = utcnow()
        db.commit()


class CatalogAdminService:
    """Admin-only catalog curation: renames, kinds, collections, album moves.

    Any manual edit sets locked=True on artists so automatic enrichment
    does not overwrite the admin's wording later.
    """

    VALID_KINDS = ("artist", "compilation", "standalone_album", "genre_collection")

    @classmethod
    def _validate_parent(cls, db: Session, artist_id: Optional[int], parent_id: Optional[int]) -> None:
        """Guard the self-referencing hierarchy: target exists, no self/descendant loops.

        `artist_id` is the card being moved (None while creating a new card,
        when only the target's existence can be checked).
        """
        if parent_id is None:
            return
        if artist_id is not None and parent_id == artist_id:
            raise HTTPException(status_code=400, detail="A card cannot be nested inside itself.")
        target = ArtistRepository.get_by_id(db, parent_id)
        if not target:
            raise HTTPException(status_code=404, detail="Target parent card not found.")
        if artist_id is not None and artist_id in ArtistRepository.ancestor_ids(db, parent_id):
            raise HTTPException(
                status_code=400,
                detail="Cannot nest a card inside its own descendant (cycle).",
            )

    @classmethod
    def update_artist(
        cls,
        db: Session,
        artist_id: int,
        display_name: Optional[str] = None,
        kind: Optional[str] = None,
        bio: Optional[str] = None,
        locked: Optional[bool] = None,
        parent_id: Optional[int] = None,
        set_parent: bool = False,
    ) -> MusicArtist:
        artist = ArtistRepository.get_by_id(db, artist_id)
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found.")

        touched = False
        if display_name is not None:
            name = display_name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="display_name must not be empty.")
            if len(name) > 200:
                raise HTTPException(status_code=400, detail="display_name is too long (max 200).")
            artist.display_name = name
            artist.search_name = name.lower()
            artist.locked = True
            touched = True
        if kind is not None:
            if kind not in cls.VALID_KINDS:
                raise HTTPException(status_code=400, detail=f"Invalid kind. Allowed: {', '.join(cls.VALID_KINDS)}.")
            artist.kind = kind
            touched = True
        if bio is not None:
            artist.bio = bio.strip() or None
            touched = True
        if locked is not None:
            artist.locked = bool(locked)
            touched = True
        if set_parent:
            # Re-parent (int) or detach to root (explicit null) — the drag-and-drop
            # curation primitive. Validated before the first write so a cycle
            # cannot leave the row half-updated.
            cls._validate_parent(db, artist.id, parent_id)
            if artist.parent_id != parent_id:
                artist.parent_id = parent_id
                touched = True
        if touched:
            artist.updated_at = utcnow()
            db.commit()
            db.refresh(artist)
        return artist

    @classmethod
    def create_collection(
        cls,
        db: Session,
        display_name: str,
        kind: str = "genre_collection",
        parent_id: Optional[int] = None,
        bio: Optional[str] = None,
    ) -> MusicArtist:
        from app.models.vfs import Folder, FolderClosure

        name = (display_name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="display_name must not be empty.")
        if len(name) > 200:
            raise HTTPException(status_code=400, detail="display_name is too long (max 200).")
        if kind not in cls.VALID_KINDS:
            raise HTTPException(status_code=400, detail=f"Invalid kind. Allowed: {', '.join(cls.VALID_KINDS)}.")

        folder = Folder(name=name)
        db.add(folder)
        db.flush()  # assign folder.id without committing
        db.add(FolderClosure(ancestor_id=folder.id, descendant_id=folder.id, depth=0))
        artist = MusicArtist(
            folder_id=folder.id,
            kind=kind,
            display_name=name,
            search_name=name.lower(),
            locked=True,  # hand-made entries are never auto-enriched
        )
        if bio and bio.strip():
            artist.bio = bio.strip()
        if parent_id is not None:
            # Validate before flush of the parent FK so a bad id rolls the
            # whole creation back instead of committing an orphan folder.
            cls._validate_parent(db, None, parent_id)
            artist.parent_id = parent_id
        db.add(artist)
        db.commit()
        db.refresh(artist)
        return artist

    @staticmethod
    def _deletion_blocker(db: Session, artist) -> Optional[dict]:
        """Why this card cannot be deleted *safely*, or None when it can.

        Only the non-cascading path uses this: the admin deletes a card
        entirely (albums included), so there is nothing to block it.
        """
        album_count = AlbumRepository.count_by_artist(db, artist.id)
        if album_count:
            return {
                "code": "has_albums",
                "detail": f"Cannot delete: {album_count} album(s) still belong here. Move them first.",
            }
        file_count = FileRepository.count_in_folder(db, artist.folder_id)
        if file_count:
            return {
                "code": "has_files",
                "detail": f"Cannot delete: {file_count} file(s) still reference this folder.",
            }
        return None

    @staticmethod
    def _purge_unused_cover_keys(db: Session, keys: List[str]) -> None:
        """Best-effort disk cleanup for cover keys nothing references any more.

        Custom covers are keyed by content hash, so the same key can be shared
        by several entities; a key is purged only when neither artist, album,
        nor any other file still points at it. Runs after commit — a failure
        here must never fail the deletion.
        """
        from sqlalchemy import func, select

        for key in dict.fromkeys(k for k in keys if k):
            try:
                still_referenced = (
                    db.scalar(select(func.count())
                              .where(MusicArtist.custom_image_key == key))
                    or 0
                ) + (
                    db.scalar(select(func.count())
                              .where(MusicAlbum.custom_image_key == key))
                    or 0
                ) + (
                    db.scalar(select(func.count())
                              .where(VFile.custom_image_key == key))
                    or 0
                )
                if not still_referenced:
                    cover_cache.purge_key(key)
            except Exception:  # noqa: BLE001 — cleanup is cosmetic
                logger.debug("Cover key purge failed for %s", key, exc_info=True)

    @staticmethod
    def delete_artist(
        db: Session,
        artist_id: int,
        with_albums: bool = False,
        with_files: bool = False,
    ) -> dict:
        """Delete an artist/collection card.

        `with_albums=False` keeps the old safe behaviour for programmatic
        callers: refuse while albums or files remain. The admin deletes with
        `with_albums=True` — the card goes entirely, its album rows go with it.

        `with_files=True` (admin-only) additionally removes the file rows
        inside the card's folder — the track rows of the card's albums leave
        the database together with the card. Only rows in the card's own folder
        are touched: nested cards are detached first and keep their own
        folders and files. Playback/playlists/share/history rows follow via
        ON DELETE CASCADE. The audio copies in the remote storage are never
        touched, and rows already in the trash stay. Unreferenced custom cover
        images are purged from the disk cache.
        """
        artist = ArtistRepository.get_by_id(db, artist_id)
        if not artist:
            raise HTTPException(status_code=404, detail="Artist not found.")

        album_count = AlbumRepository.count_by_artist(db, artist.id)
        file_count = FileRepository.count_in_folder(db, artist.folder_id)
        if not with_albums:
            blocker = CatalogAdminService._deletion_blocker(db, artist)
            if blocker:
                raise HTTPException(status_code=400, detail=blocker["detail"])

        folder_id = artist.folder_id
        cover_keys = []
        if with_files:
            cover_keys = FileRepository.custom_cover_keys_in_folder(db, folder_id)
        # Albums are catalog metadata of the card, not separate objects: the
        # `MusicArtist.albums` relationship cascades (delete-orphan) and the FK
        # is ON DELETE CASCADE, so they go with the card. `album_count` is read
        # before the delete purely for the report.
        deleted_albums = album_count
        # Detach nested cards: they are independent catalog entries and must
        # survive the parent's deletion (never cascade into real artists).
        detached = ArtistRepository.detach_children(db, artist.id)
        db.delete(artist)
        deleted_files = 0
        if with_files:
            # The card's tracks go with it: every non-trash file row in the
            # card's own folder. Nested cards live in their own folders, so
            # they cannot be hit here.
            deleted_files = FileRepository.delete_in_folder(db, folder_id)
        db.flush()
        # Remove the backing folder only if nothing else references it — files
        # keep their folder alive, and the file rows themselves are never deleted.
        still_used = (
            ArtistRepository.count_by_folder(db, folder_id)
            + AlbumRepository.count_by_folder(db, folder_id)
            + FolderRepository.count_children(db, folder_id)
            + FileRepository.count_in_folder(db, folder_id)
        )
        if not still_used:
            FolderRepository.delete_closures_for(db, folder_id)
            folder = FolderRepository.get(db, folder_id)
            if folder:
                FolderRepository.delete(db, folder)
        db.commit()
        if cover_keys:
            CatalogAdminService._purge_unused_cover_keys(db, cover_keys)
        return {
            "ok": True,
            "id": artist_id,
            "detached_children": detached or 0,
            "deleted_albums": deleted_albums,
            "deleted_files": deleted_files,
            "files_kept": file_count if not with_files else 0,
        }

    @classmethod
    def delete_artists(cls, db: Session, artist_ids: List[int], with_files: bool = True) -> dict:
        """Delete several cards entirely, reporting per-card outcome.

        Every selected card goes, albums included — the admin asked for «удалить
        целиком». With `with_files=True` (the default) the tracks of the card's
        albums leave the database too. Only a card that vanished or blew up is
        reported in `skipped`, and it never aborts the rest. Each card commits
        on its own.
        """
        deleted: List[int] = []
        skipped: List[dict] = []
        deleted_albums = 0
        deleted_files = 0

        for artist_id in artist_ids:
            artist = ArtistRepository.get_by_id(db, artist_id)
            if not artist:
                skipped.append({"id": artist_id, "code": "not_found"})
                continue
            try:
                result = cls.delete_artist(db, artist_id, with_albums=True, with_files=with_files)
            except HTTPException as exc:
                db.rollback()
                skipped.append({"id": artist_id, "code": "refused", "detail": exc.detail})
            except Exception:  # noqa: BLE001 — one bad card must not stop the batch
                db.rollback()
                logger.exception("Bulk catalog delete failed for card %s", artist_id)
                skipped.append({"id": artist_id, "code": "error"})
            else:
                deleted.append(artist_id)
                deleted_albums += result.get("deleted_albums", 0)
                deleted_files += result.get("deleted_files", 0)

        return {
            "ok": True,
            "deleted": deleted,
            "skipped": skipped,
            "deleted_count": len(deleted),
            "skipped_count": len(skipped),
            "deleted_albums": deleted_albums,
            "deleted_files": deleted_files,
        }

    @staticmethod
    def update_album(
        db: Session,
        album_id: int,
        title: Optional[str] = None,
        year: Optional[int] = None,
        artist_id: Optional[int] = None,
    ) -> MusicAlbum:
        album = AlbumRepository.get_by_id(db, album_id)
        if not album:
            raise HTTPException(status_code=404, detail="Album not found.")

        touched = False
        if title is not None:
            name = title.strip()
            if not name:
                raise HTTPException(status_code=400, detail="title must not be empty.")
            if len(name) > 300:
                raise HTTPException(status_code=400, detail="title is too long (max 300).")
            album.title = name
            touched = True
        if year is not None:
            if year == 0:
                album.year = None
                touched = True
            elif 1800 <= year <= 2100:
                album.year = year
                touched = True
            else:
                raise HTTPException(status_code=400, detail="year must be between 1800 and 2100 (or 0 to clear).")
        if artist_id is not None:
            if artist_id == album.artist_id:
                pass
            else:
                target = ArtistRepository.get_by_id(db, artist_id)
                if not target:
                    raise HTTPException(status_code=404, detail="Target artist/collection not found.")
                album.artist_id = target.id
                touched = True
        if touched:
            album.updated_at = utcnow()
            db.commit()
            db.refresh(album)
        return album

    @classmethod
    def classify_node_kind(
        cls,
        depth: int,
        has_direct_audio: bool,
        has_child_audio: bool,
        catalog_kind: str = "compilation",
    ) -> str:
        """Catalog kind for one folder node of a synchronized tree.

        Folder *shape* must not decide the kind: in a discography tree
        (Artist → «01.Albums» → album → «Covers») the album folder is a release,
        yet «has subfolders» used to make it a `compilation` and «leaf with
        audio» made it an `artist`. That is exactly how albums ended up in the
        admin list labelled «Артист».

        - depth 0 — the root of the tree, kind comes from the caller;
        - a node holding audio itself, with no audio below it, IS a release
          → `standalone_album`;
        - anything else is a grouping folder (album group, box set, artist
          folder) → `genre_collection`, never «Сборник»: `compilation` means a
          multi-artist collection, which folder shape cannot prove.
        """
        if depth == 0:
            return catalog_kind if catalog_kind in cls.VALID_KINDS else "compilation"
        if has_direct_audio and not has_child_audio:
            return "standalone_album"
        return "genre_collection"

    @classmethod
    def sync_catalog_folder_tree(
        cls,
        db: Session,
        root_folder_id: int,
        catalog_kind: str = "compilation",
        commit: bool = True,
    ) -> dict:
        """Recursively synchronizes an arbitrary nested folder tree into catalog artists and albums.

        Containers with subfolders become grouping cards (`genre_collection`),
        folders that hold audio themselves become releases (`standalone_album`).
        The root node keeps `catalog_kind`. Ghost/empty album records under
        containers are cleaned up. See `classify_node_kind` for the rule.
        """
        from app.models.vfs import Folder, VFile
        from sqlalchemy import select, func

        stats = {
            "created_artists": 0,
            "updated_artists": 0,
            "created_albums": 0,
            "deleted_empty_albums": 0,
        }

        root_folder = FolderRepository.get(db, root_folder_id)
        if not root_folder or root_folder.is_trash:
            raise HTTPException(status_code=404, detail="Root folder not found.")

        def _sync_node(folder: Folder, parent_artist_id: Optional[int], depth: int = 0):
            subfolders = db.execute(
                select(Folder).where(Folder.parent_id == folder.id, Folder.is_trash == False)
            ).scalars().all()
            # Audio only: a folder of scans/covers must not count as a release.
            audio_count = db.execute(
                select(func.count(VFile.id)).where(
                    VFile.folder_id == folder.id,
                    VFile.is_trash == False,
                    VFile.mime_type.like("audio/%"),
                )
            ).scalar() or 0
            # Audio one level down tells a release folder apart from a grouping
            # folder: «1987 - Дерьмонтин» holds the tracks, its «Covers» child
            # does not; a box set holds nothing and keeps the discs below.
            child_audio_count = db.execute(
                select(func.count(VFile.id))
                .join(Folder, Folder.id == VFile.folder_id)
                .where(
                    Folder.parent_id == folder.id,
                    Folder.is_trash == False,
                    VFile.is_trash == False,
                    VFile.mime_type.like("audio/%"),
                )
            ).scalar() or 0

            # Ignore empty folders with no subfolders and no audio
            if not subfolders and audio_count == 0:
                return

            artist = db.execute(
                select(MusicArtist).where(MusicArtist.folder_id == folder.id)
            ).scalars().first()

            kind = cls.classify_node_kind(
                depth,
                has_direct_audio=audio_count > 0,
                has_child_audio=child_audio_count > 0,
                catalog_kind=catalog_kind,
            )

            if not artist:
                artist = MusicArtist(
                    folder_id=folder.id,
                    kind=kind,
                    parent_id=parent_artist_id,
                    display_name=folder.name,
                    search_name=folder.name.casefold(),
                    confidence=100,
                    locked=True,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                db.add(artist)
                db.flush()
                stats["created_artists"] += 1
            else:
                changed = False
                if artist.kind != kind:
                    artist.kind = kind
                    changed = True
                if artist.parent_id != parent_artist_id:
                    artist.parent_id = parent_artist_id
                    changed = True
                if changed:
                    artist.updated_at = utcnow()
                    stats["updated_artists"] += 1

            album = db.execute(
                select(MusicAlbum).where(MusicAlbum.folder_id == folder.id)
            ).scalars().first()

            if audio_count > 0:
                if not album:
                    album = MusicAlbum(
                        folder_id=folder.id,
                        artist_id=artist.id,
                        title=folder.name,
                        confidence=100,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                    db.add(album)
                    stats["created_albums"] += 1
                else:
                    if album.artist_id != artist.id:
                        album.artist_id = artist.id
                        album.updated_at = utcnow()
            else:
                if album:
                    db.delete(album)
                    stats["deleted_empty_albums"] += 1

            for sf in subfolders:
                _sync_node(sf, artist.id, depth + 1)

        _sync_node(root_folder, None, 0)
        if commit:
            db.commit()
        else:
            db.flush()
        return stats


library_service = MusicLibraryService()

