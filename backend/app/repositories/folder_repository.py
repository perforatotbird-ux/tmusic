"""
T-Music Folder Repository — data access for VFS folders and closures.
"""
from typing import List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.vfs import Folder, FolderClosure


class FolderRepository:
    @staticmethod
    def list_all(db: Session) -> List[Folder]:
        return list(db.scalars(select(Folder)).all())

    @staticmethod
    def list_by_ids(db: Session, folder_ids) -> List[Folder]:
        return list(db.scalars(select(Folder).where(Folder.id.in_(folder_ids))).all())

    @staticmethod
    def count_children(db: Session, folder_id: int) -> int:
        return db.execute(
            select(func.count(Folder.id)).where(Folder.parent_id == folder_id)
        ).scalar() or 0

    @staticmethod
    def get(db: Session, folder_id: int) -> Optional[Folder]:
        return db.get(Folder, folder_id)

    @staticmethod
    def delete(db: Session, folder: Folder) -> None:
        db.delete(folder)

    @staticmethod
    def delete_all_closures(db: Session) -> None:
        db.execute(delete(FolderClosure))

    @staticmethod
    def delete_closures_for(db: Session, folder_id: int) -> None:
        db.execute(
            delete(FolderClosure).where(
                (FolderClosure.ancestor_id == folder_id) | (FolderClosure.descendant_id == folder_id)
            )
        )
