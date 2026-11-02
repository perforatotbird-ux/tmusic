"""
T-Music Search Repository
Database access layer for high-speed catalog searching.
Strictly isolated from HTTP/FastAPI concerns (ADR-001).
"""
import logging
from typing import List, Dict, Any, Tuple
from sqlalchemy import select, func, case, or_, text
from sqlalchemy.orm import Session

from app.models.music import MusicArtist, MusicAlbum
from app.models.vfs import VFile, FolderClosure
from app.utils.like import escape_like

logger = logging.getLogger("tmusic.search_repo")


class SearchRepository:
    """Repository handling SQL queries for search across artists, albums, and tracks."""

    @staticmethod
    def ensure_indexes(db: Session) -> None:
        """Ensure GIN trigram indexes exist in PostgreSQL for sub-millisecond search."""
        try:
            dialect = db.bind.dialect.name if db.bind else ""
            if dialect == "postgresql":
                db.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm;"))
                db.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_vfiles_name_trgm
                    ON vfiles USING gin (lower(name) gin_trgm_ops)
                    WHERE is_trash = false AND mime_type LIKE 'audio/%';
                """))
                db.commit()
                logger.info("Search trigram indexes verified on PostgreSQL.")
        except Exception as e:
            logger.warning("Search index setup skipped or failed (non-fatal): %s", e)
            try:
                db.rollback()
            except Exception:
                logger.debug("Rollback after search index setup failure failed", exc_info=True)

    @staticmethod
    def search_artists(
        db: Session,
        query: str,
        limit: int = 10,
        offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Search artists by display_name or search_name."""
        trimmed = query.strip()
        if not trimmed:
            return [], 0

        exact_lower = trimmed.lower()
        pattern = f"%{escape_like(exact_lower)}%"
        prefix_pattern = f"{escape_like(exact_lower)}%"

        filter_condition = or_(
            func.lower(MusicArtist.display_name).like(pattern, escape="\\"),
            func.lower(MusicArtist.search_name).like(pattern, escape="\\"),
        )

        rank_order = case(
            (func.lower(MusicArtist.display_name) == exact_lower, 1),
            (func.lower(MusicArtist.display_name).like(prefix_pattern, escape="\\"), 2),
            else_=3
        )

        # Subquery for album count
        album_count_subq = (
            select(func.count(MusicAlbum.id))
            .where(MusicAlbum.artist_id == MusicArtist.id)
            .correlate(MusicArtist)
            .scalar_subquery()
            .label("album_count")
        )

        stmt = (
            select(MusicArtist, album_count_subq)
            .where(filter_condition)
            .order_by(rank_order, MusicArtist.display_name.asc())
            .offset(offset)
            .limit(limit)
        )

        rows = db.execute(stmt).all()
        # Fast total: if on first page and fewer than limit, total is exactly len(rows)
        if offset == 0 and len(rows) < limit:
            total = len(rows)
        else:
            total = db.scalar(select(func.count(MusicArtist.id)).where(filter_condition)) or 0

        results = []
        for artist, alb_cnt in rows:
            results.append({
                "id": artist.id,
                "display_name": artist.display_name,
                "kind": artist.kind,
                "image_url": artist.image_url,
                "custom_image_key": artist.custom_image_key,
                "enriched_at": artist.enriched_at,
                "album_count": alb_cnt or 0,
                "folder_id": artist.folder_id,
            })
        return results, total

    @staticmethod
    def search_albums(
        db: Session,
        query: str,
        limit: int = 10,
        offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Search albums by title with artist display_name joined in 1 query."""
        trimmed = query.strip()
        if not trimmed:
            return [], 0

        exact_lower = trimmed.lower()
        pattern = f"%{escape_like(exact_lower)}%"
        prefix_pattern = f"{escape_like(exact_lower)}%"

        filter_condition = func.lower(MusicAlbum.title).like(pattern, escape="\\")

        rank_order = case(
            (func.lower(MusicAlbum.title) == exact_lower, 1),
            (func.lower(MusicAlbum.title).like(prefix_pattern, escape="\\"), 2),
            else_=3
        )

        stmt = (
            select(
                MusicAlbum.id,
                MusicAlbum.title,
                MusicAlbum.year,
                MusicAlbum.artist_id,
                MusicAlbum.folder_id,
                MusicAlbum.cover_url,
                MusicAlbum.custom_image_key,
                MusicAlbum.enriched_at,
                MusicArtist.display_name.label("artist_name"),
            )
            .join(MusicArtist, MusicArtist.id == MusicAlbum.artist_id)
            .where(filter_condition)
            .order_by(rank_order, MusicAlbum.title.asc())
            .offset(offset)
            .limit(limit)
        )

        rows = db.execute(stmt).all()
        if offset == 0 and len(rows) < limit:
            total = len(rows)
        else:
            total = db.scalar(select(func.count(MusicAlbum.id)).where(filter_condition)) or 0

        results = []
        for r in rows:
            results.append({
                "id": r.id,
                "title": r.title,
                "year": r.year,
                "artist_id": r.artist_id,
                "artist_name": r.artist_name,
                "cover_url": r.cover_url,
                "custom_image_key": r.custom_image_key,
                "enriched_at": r.enriched_at,
                "folder_id": r.folder_id,
            })
        return results, total

    @staticmethod
    def search_tracks(
        db: Session,
        query: str,
        limit: int = 20,
        offset: int = 0
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Search tracks by file name and enrich with album/artist metadata in 1 batch query.
        Uses FolderClosure with depth ordering to find closest album ancestor (handles multi-disc CDs).
        """
        trimmed = query.strip()
        if not trimmed:
            return [], 0

        exact_lower = trimmed.lower()
        pattern = f"%{escape_like(exact_lower)}%"
        prefix_pattern = f"{escape_like(exact_lower)}%"

        filter_condition = (
            (VFile.is_trash == False) &  # noqa: E712
            (VFile.mime_type.like("audio/%")) &
            (func.lower(VFile.name).like(pattern, escape="\\"))
        )

        rank_order = case(
            (func.lower(VFile.name) == exact_lower, 1),
            (func.lower(VFile.name).like(prefix_pattern, escape="\\"), 2),
            else_=3
        )

        stmt = (
            select(
                VFile.id,
                VFile.name,
                VFile.size,
                VFile.mime_type,
                VFile.folder_id,
                VFile.custom_image_key,
            )
            .where(filter_condition)
            .order_by(rank_order, VFile.name.asc())
            .offset(offset)
            .limit(limit)
        )

        tracks = db.execute(stmt).all()
        if offset == 0 and len(tracks) < limit:
            total = len(tracks)
        else:
            total = db.scalar(select(func.count(VFile.id)).where(filter_condition)) or 0

        if not tracks:
            return [], total

        # Batch resolve album and artist via folder_closures for all returned tracks
        folder_ids = list({t.folder_id for t in tracks if t.folder_id is not None})
        meta_by_folder: Dict[int, Dict[str, Any]] = {}

        if folder_ids:
            # Query album closures ordered by depth ASC so the nearest album wins
            closure_stmt = (
                select(
                    FolderClosure.descendant_id,
                    FolderClosure.depth,
                    MusicAlbum.id.label("album_id"),
                    MusicAlbum.title.label("album_title"),
                    MusicAlbum.cover_url.label("album_cover_url"),
                    MusicAlbum.custom_image_key.label("album_custom_key"),
                    MusicAlbum.enriched_at.label("album_enriched_at"),
                    MusicArtist.id.label("artist_id"),
                    MusicArtist.display_name.label("artist_name"),
                )
                .join(MusicAlbum, MusicAlbum.folder_id == FolderClosure.ancestor_id)
                .join(MusicArtist, MusicArtist.id == MusicAlbum.artist_id)
                .where(FolderClosure.descendant_id.in_(folder_ids))
                .order_by(FolderClosure.descendant_id, FolderClosure.depth.asc())
            )
            closure_rows = db.execute(closure_stmt).all()
            for r in closure_rows:
                # Keep first match (lowest depth)
                if r.descendant_id not in meta_by_folder:
                    meta_by_folder[r.descendant_id] = {
                        "album_id": r.album_id,
                        "album_title": r.album_title,
                        "cover_url": r.album_cover_url,
                        "custom_image_key": r.album_custom_key,
                        "enriched_at": r.album_enriched_at,
                        "artist_id": r.artist_id,
                        "artist_name": r.artist_name,
                    }

            # For any folders not matching an album, check direct artist folders
            missing_folders = [fid for fid in folder_ids if fid not in meta_by_folder]
            if missing_folders:
                artist_stmt = (
                    select(
                        MusicArtist.folder_id,
                        MusicArtist.id.label("artist_id"),
                        MusicArtist.display_name.label("artist_name"),
                        MusicArtist.image_url.label("artist_image_url"),
                        MusicArtist.custom_image_key.label("artist_custom_key"),
                        MusicArtist.enriched_at.label("artist_enriched_at"),
                    )
                    .where(MusicArtist.folder_id.in_(missing_folders))
                )
                artist_rows = db.execute(artist_stmt).all()
                for r in artist_rows:
                    meta_by_folder[r.folder_id] = {
                        "album_id": None,
                        "album_title": None,
                        "cover_url": r.artist_image_url,
                        "custom_image_key": r.artist_custom_key,
                        "enriched_at": r.artist_enriched_at,
                        "artist_id": r.artist_id,
                        "artist_name": r.artist_name,
                    }

        results = []
        for t in tracks:
            meta = meta_by_folder.get(t.folder_id, {})
            results.append({
                "id": t.id,
                "file_name": t.name,
                "size": t.size,
                "mime_type": t.mime_type,
                "folder_id": t.folder_id,
                "album_id": meta.get("album_id"),
                "album_title": meta.get("album_title"),
                "artist_id": meta.get("artist_id"),
                "artist_name": meta.get("artist_name"),
                "cover_url": meta.get("cover_url"),
                # A track's own artwork wins over the album/artist one; the
                # album key stays as the inheritance fallback.
                "custom_image_key": t.custom_image_key or meta.get("custom_image_key"),
                "enriched_at": meta.get("enriched_at"),
            })

        return results, total
