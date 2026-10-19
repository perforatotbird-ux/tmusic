"""
T-Music Artist Repository
Data access layer for MusicArtist entities.
"""
from typing import Optional, List, Tuple
from sqlalchemy import select, func
from sqlalchemy.orm import Session, selectinload, load_only

from app.models.music import MusicArtist, MusicAlbum
from app.utils.like import like_pattern


# Columns required by MusicArtistCard (list view) — avoids loading bio/wiki Text blobs.
_ARTIST_CARD_COLUMNS = (
    MusicArtist.id,
    MusicArtist.folder_id,
    MusicArtist.kind,
    MusicArtist.parent_id,
    MusicArtist.display_name,
    MusicArtist.custom_image_key,
    MusicArtist.image_url,
    MusicArtist.confidence,
    MusicArtist.enriched_at,
)


class ArtistRepository:
    """Repository managing MusicArtist database operations."""

    @staticmethod
    def get_by_id(db: Session, artist_id: int) -> Optional[MusicArtist]:
        stmt = (
            select(MusicArtist)
            .where(MusicArtist.id == artist_id)
            .options(selectinload(MusicArtist.albums))
        )
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def get_by_folder_id(db: Session, folder_id: int) -> Optional[MusicArtist]:
        stmt = select(MusicArtist).where(MusicArtist.folder_id == folder_id)
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_artists(
        db: Session,
        kind: Optional[str] = None,
        search: Optional[str] = None,
        root_only: Optional[bool] = None,
        limit: int = 200,
        offset: int = 0
    ) -> List[Tuple[MusicArtist, int, Optional[str], int]]:
        """List artists with album_count, parent_name, children_count.

        All three aggregates ride as correlated subqueries in one round trip —
        no join fan-out and no lazy loads while building the card list.
        """
        album_count = (
            select(func.count(MusicAlbum.id))
            .where(MusicAlbum.artist_id == MusicArtist.id)
            .correlate(MusicArtist)
            .scalar_subquery()
            .label("album_count")
        )
        parent = select(MusicArtist).alias("parent_entity")
        parent_name = (
            select(parent.c.display_name)
            .where(parent.c.id == MusicArtist.parent_id)
            .correlate(MusicArtist)
            .scalar_subquery()
            .label("parent_name")
        )
        child = select(MusicArtist).alias("child_entity")
        children_count = (
            select(func.count(child.c.id))
            .where(child.c.parent_id == MusicArtist.id)
            .correlate(MusicArtist)
            .scalar_subquery()
            .label("children_count")
        )
        stmt = (
            select(MusicArtist, album_count, parent_name, children_count)
            .options(load_only(*_ARTIST_CARD_COLUMNS))
        )
        if kind and kind not in ("null", "undefined"):
            stmt = stmt.where(MusicArtist.kind == kind)
        has_search = bool(search and search not in ("null", "undefined") and search.strip())
        if has_search:
            pattern = like_pattern(search)
            stmt = stmt.where(
                func.lower(MusicArtist.display_name).like(pattern, escape="\\") |
                func.lower(MusicArtist.search_name).like(pattern, escape="\\")
            )
        if root_only is True:
            stmt = stmt.where(MusicArtist.parent_id.is_(None))

        stmt = stmt.order_by(MusicArtist.display_name.asc()).offset(offset).limit(limit)
        return list(db.execute(stmt).all())

    @staticmethod
    def count(db: Session, kind: Optional[str] = None) -> int:
        stmt = select(func.count(MusicArtist.id))
        if kind:
            stmt = stmt.where(MusicArtist.kind == kind)
        return db.execute(stmt).scalar() or 0

    @staticmethod
    def list_children(
        db: Session, parent_id: int
    ) -> List[Tuple[MusicArtist, int, Optional[str], int]]:
        """Direct children of a catalog card, ordered by display name, with counts."""
        album_count = (
            select(func.count(MusicAlbum.id))
            .where(MusicAlbum.artist_id == MusicArtist.id)
            .correlate(MusicArtist)
            .scalar_subquery()
            .label("album_count")
        )
        parent = select(MusicArtist).alias("parent_entity")
        parent_name = (
            select(parent.c.display_name)
            .where(parent.c.id == MusicArtist.parent_id)
            .correlate(MusicArtist)
            .scalar_subquery()
            .label("parent_name")
        )
        child = select(MusicArtist).alias("child_entity")
        children_count = (
            select(func.count(child.c.id))
            .where(child.c.parent_id == MusicArtist.id)
            .correlate(MusicArtist)
            .scalar_subquery()
            .label("children_count")
        )
        stmt = (
            select(MusicArtist, album_count, parent_name, children_count)
            .options(load_only(*_ARTIST_CARD_COLUMNS))
            .where(MusicArtist.parent_id == parent_id)
            .order_by(MusicArtist.display_name.asc())
        )
        return list(db.execute(stmt).all())

    @staticmethod
    def ancestor_ids(db: Session, artist_id: int, max_depth: int = 64) -> List[int]:
        """Chain of parent ids above artist_id (nearest parent first).

        The depth cap is a self-healing guard against reference cycles that
        could have been written outside the service layer.
        """
        chain: List[int] = []
        current_id: Optional[int] = artist_id
        for _ in range(max_depth):
            if current_id is None:
                break
            row = db.execute(
                select(MusicArtist.parent_id).where(MusicArtist.id == current_id)
            ).scalar_one_or_none()
            if row is None:
                break
            chain.append(row)
            current_id = row
        return chain

    @staticmethod
    def get_ancestors(db: Session, artist_id: int, max_depth: int = 64) -> List[MusicArtist]:
        """Chain of parent entities above artist_id, ordered from root down to immediate parent."""
        ancestor_ids = ArtistRepository.ancestor_ids(db, artist_id, max_depth=max_depth)
        if not ancestor_ids:
            return []
        stmt = (
            select(MusicArtist)
            .options(load_only(*_ARTIST_CARD_COLUMNS))
            .where(MusicArtist.id.in_(ancestor_ids))
        )
        entities_by_id = {a.id: a for a in db.execute(stmt).scalars().all()}
        # Reconstruct in root-to-parent order (reverse of nearest-first)
        result = []
        for aid in reversed(ancestor_ids):
            if aid in entities_by_id:
                result.append(entities_by_id[aid])
        return result

    @staticmethod
    def count_children(db: Session, artist_id: int) -> int:
        return db.execute(
            select(func.count(MusicArtist.id)).where(MusicArtist.parent_id == artist_id)
        ).scalar() or 0

    @staticmethod
    def count_by_folder(db: Session, folder_id: int) -> int:
        return db.execute(
            select(func.count(MusicArtist.id)).where(MusicArtist.folder_id == folder_id)
        ).scalar() or 0

    @staticmethod
    def detach_children(db: Session, artist_id: int) -> int:
        """Detach nested cards (parent_id=NULL) so they survive the parent's deletion."""
        return db.execute(
            MusicArtist.__table__.update()
            .where(MusicArtist.parent_id == artist_id)
            .values(parent_id=None)
        ).rowcount
