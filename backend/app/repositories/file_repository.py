"""
T-Music File Repository
Data access layer for audio VFiles with natural sorting.
"""
from typing import Optional, List
from sqlalchemy import select, func, Numeric
from sqlalchemy.orm import Session, load_only

from app.models.vfs import VFile, FolderClosure


# Columns needed for track list cards + plaintext size calculation.
# Excludes encryption_header / account_map / note / msg_ids (heavy Text/JSON).
_TRACK_LIST_COLUMNS = (
    VFile.id,
    VFile.name,
    VFile.size,
    VFile.mime_type,
    VFile.folder_id,
    VFile.is_encrypted,
    VFile.encryption_salt,
    VFile.created_at,
)


def _natural_sort_columns():
    num_part = func.cast(func.substring(VFile.name, r"([0-9]+)"), Numeric)
    prefix = func.substring(VFile.name, r"^([^0-9]*)")
    return prefix, num_part


class FileRepository:
    """Repository managing audio VFiles and track collections."""

    @staticmethod
    def get_by_id(db: Session, file_id: int) -> Optional[VFile]:
        stmt = select(VFile).where(VFile.id == file_id, VFile.is_trash.is_(False))
        return db.execute(stmt).scalar_one_or_none()

    @staticmethod
    def list_tracks_in_folder(
        db: Session,
        folder_id: int,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[VFile]:
        """List audio tracks directly inside a folder with natural sorting."""
        prefix, num_part = _natural_sort_columns()

        stmt = (
            select(VFile)
            .options(load_only(*_TRACK_LIST_COLUMNS))
            .where(
                VFile.folder_id == folder_id,
                VFile.is_trash.is_(False),
                VFile.mime_type.like("audio/%")
            )
            .order_by(prefix.asc(), num_part.asc().nulls_last(), VFile.name.asc())
        )
        if limit is not None:
            stmt = stmt.offset(offset).limit(limit)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def list_tracks_recursive(
        db: Session,
        folder_id: int,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[VFile]:
        """List all audio tracks in folder and all subfolders (multi-disc, CD1/CD2) with natural sort."""
        prefix, num_part = _natural_sort_columns()

        stmt = (
            select(VFile)
            .options(load_only(*_TRACK_LIST_COLUMNS))
            .join(FolderClosure, VFile.folder_id == FolderClosure.descendant_id)
            .where(
                FolderClosure.ancestor_id == folder_id,
                VFile.is_trash.is_(False),
                VFile.mime_type.like("audio/%")
            )
            .order_by(FolderClosure.depth.asc(), prefix.asc(), num_part.asc().nulls_last(), VFile.name.asc())
        )
        if limit is not None:
            stmt = stmt.offset(offset).limit(limit)
        return list(db.execute(stmt).scalars().all())

    @staticmethod
    def count_tracks(db: Session) -> int:
        stmt = select(func.count(VFile.id)).where(
            VFile.is_trash.is_(False),
            VFile.mime_type.like("audio/%")
        )
        return db.execute(stmt).scalar() or 0

    @staticmethod
    def total_size_bytes(db: Session) -> int:
        stmt = select(func.sum(VFile.size)).where(
            VFile.is_trash.is_(False),
            VFile.mime_type.like("audio/%")
        )
        return db.execute(stmt).scalar() or 0

    @staticmethod
    def names_in_folders(db: Session, folder_ids) -> list:
        """(folder_id, name) rows for the given folders (non-trash)."""
        return db.execute(
            select(VFile.folder_id, VFile.name).where(
                VFile.folder_id.in_(folder_ids),
                VFile.is_trash.is_(False),
            )
        ).all()

    @staticmethod
    def count_in_folder(db: Session, folder_id: int) -> int:
        return db.execute(
            select(func.count(VFile.id)).where(VFile.folder_id == folder_id)
        ).scalar() or 0

    @staticmethod
    def count_audio_in_folder(db: Session, folder_id: int) -> int:
        return db.execute(
            select(func.count(VFile.id)).where(
                VFile.folder_id == folder_id,
                VFile.is_trash.is_(False),
                VFile.mime_type.like("audio/%"),
            )
        ).scalar() or 0

    @staticmethod
    def first_album_track(db: Session, folder_id: int) -> Optional[VFile]:
        """First playable audio track of an album folder (enrichment helper)."""
        return db.scalar(
            select(VFile).where(
                VFile.folder_id == folder_id,
                VFile.is_trash.is_(False),
                VFile.mime_type.like("audio/%"),
            ).limit(1)
        )
