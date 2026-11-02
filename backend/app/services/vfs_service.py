"""
T-Music VFS folder browsing service.

Folder listing + count queries for the Windows-style hierarchy API. Keeps the
router thin (routing/validation only); DB access lives here.
"""
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.vfs import Folder, FolderClosure, VFile

MAX_BREADCRUMB_DEPTH = 32


class VfsService:
    @staticmethod
    def folder_payload(db: Session, folder: Folder, parent_id: Optional[int]) -> dict:
        """Folder card with immediate subfolder/file counts + recursive audio count."""
        subfolders = db.scalar(
            select(func.count(Folder.id)).where(
                Folder.parent_id == folder.id,
                Folder.is_trash == False,  # noqa: E712
            )
        ) or 0
        tracks = db.scalar(
            select(func.count(VFile.id)).where(
                VFile.folder_id == folder.id,
                VFile.is_trash == False,  # noqa: E712
                VFile.mime_type.like("audio/%"),
            )
        ) or 0
        recursive_tracks = db.scalar(
            select(func.count(func.distinct(VFile.id)))
            .join(FolderClosure, VFile.folder_id == FolderClosure.descendant_id)
            .where(
                FolderClosure.ancestor_id == folder.id,
                VFile.is_trash == False,  # noqa: E712
                VFile.mime_type.like("audio/%"),
            )
        ) or 0
        return {
            "id": folder.id,
            "name": folder.name,
            "parent_id": parent_id if parent_id is not None else folder.parent_id,
            "is_trash": bool(folder.is_trash),
            "is_starred": bool(folder.is_starred),
            "color": folder.color,
            "subfolder_count": int(subfolders),
            "track_count": int(tracks),
            "recursive_track_count": int(recursive_tracks),
            "created_at": folder.created_at.isoformat() if folder.created_at else None,
            "updated_at": folder.updated_at.isoformat() if folder.updated_at else None,
        }

    @staticmethod
    def list_roots(db: Session) -> list:
        """Top-level folders (parent_id is NULL), trash excluded."""
        return list(
            db.scalars(
                select(Folder)
                .where(Folder.parent_id.is_(None), Folder.is_trash == False)  # noqa: E712
                .order_by(Folder.name)
            ).all()
        )

    @staticmethod
    def list_children(db: Session, parent_id: Optional[int]) -> list:
        """Child folders of parent_id; parent_id=None means roots."""
        stmt = select(Folder).where(Folder.is_trash == False)  # noqa: E712
        if parent_id is None:
            stmt = stmt.where(Folder.parent_id.is_(None))
        else:
            stmt = stmt.where(Folder.parent_id == parent_id)
        return list(db.scalars(stmt.order_by(Folder.name)).all())

    @staticmethod
    def get_folder(db: Session, folder_id: int) -> Optional[Folder]:
        return db.scalar(select(Folder).where(Folder.id == folder_id))

    @staticmethod
    def breadcrumb(db: Session, folder: Folder) -> list:
        crumbs = []
        cur = folder
        guard = 0
        while cur and guard < MAX_BREADCRUMB_DEPTH:
            crumbs.append({"id": cur.id, "name": cur.name})
            if cur.parent_id is None:
                break
            cur = VfsService.get_folder(db, cur.parent_id)
            guard += 1
        return list(reversed(crumbs))


vfs_service = VfsService()
