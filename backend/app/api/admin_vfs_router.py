import json
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, load_only

from app.api.dependencies import get_admin_user, get_backup_admin
from app.core.database import get_db
from app.core.rate_limiter import limiter
from app.models.auth import AdminAuditEvent, AppUser
from app.models.vfs import Folder, FolderClosure, VFile
from app.services.vfs_import_service import (
    MAX_IMPORT_BYTES,
    VfsImportError,
    get_vfs_import,
    preview_vfs_import,
    run_vfs_import,
    stage_vfs_upload,
    get_staged_children,
    preview_tree_import,
    run_tree_import,
    get_import_status,
)
from app.utils.client_ip import get_client_ip
from app.utils.time import utcnow

router = APIRouter(prefix="/api/admin/vfs", tags=["admin-vfs"])

# Per-user budget for import runs: a full import rewrites folders/closures and
# can flood Telegram with media lookups. The global per-IP window is the only
# other guard.
IMPORT_RUNS_PER_MIN = 4


def _import_budget(user: AppUser, op: str) -> None:
    if limiter.is_rate_limited(f"vfsimport:{user.id}:{op}", IMPORT_RUNS_PER_MIN, 60):
        raise HTTPException(
            status_code=429,
            detail="Too many import operations. Slow down.",
            headers={"Retry-After": "60"},
        )


class FolderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=300)
    parent_id: Optional[int] = None
    set_parent: bool = False
    is_trash: Optional[bool] = None
    is_starred: Optional[bool] = None
    color: Optional[str] = Field(default=None, max_length=32)
    note: Optional[str] = Field(default=None, max_length=5000)


class FileUpdate(BaseModel):
    folder_id: Optional[int] = None
    set_folder: bool = False
    is_trash: Optional[bool] = None
    is_starred: Optional[bool] = None
    pending_deletion: Optional[bool] = None
    note: Optional[str] = Field(default=None, max_length=5000)


class FileMove(BaseModel):
    file_ids: List[int] = Field(min_length=1, max_length=1000)
    folder_id: int


class VfsImportRunRequest(BaseModel):
    confirm: bool = False


# How a staged import is materialised in the catalog. Mirrors the four options
# offered by the admin UI; anything else used to silently fall back to
# "compilation" inside the service, which hid typos and made the request
# unbounded. Rejected with 422 now.
CatalogKind = Literal["compilation", "artist", "genre_collection", "none"]

# Bounds so a single request cannot ask the importer to walk an unbounded
# selection (the body limit does not help: ids are compact).
MAX_SELECTED_FOLDERS = 5000
MAX_SELECTED_FILES = 50000


class VfsTreePreviewRequest(BaseModel):
    selected_folder_ids: List[int] = Field(default_factory=list, max_length=MAX_SELECTED_FOLDERS)
    selected_file_ids: List[int] = Field(default_factory=list, max_length=MAX_SELECTED_FILES)
    target_root_id: Optional[int] = None
    preserve_parent_path: bool = True
    catalog_kind: CatalogKind = "compilation"


class VfsTreeRunRequest(BaseModel):
    selected_folder_ids: List[int] = Field(default_factory=list, max_length=MAX_SELECTED_FOLDERS)
    selected_file_ids: List[int] = Field(default_factory=list, max_length=MAX_SELECTED_FILES)
    target_root_id: Optional[int] = None
    confirm: bool = True
    preserve_parent_path: bool = True
    catalog_kind: CatalogKind = "compilation"


def _audit(db: Session, request: Request, user: AppUser, action: str, target_type: str, target_id: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
    db.add(AdminAuditEvent(
        actor_id=user.id,
        actor_username=user.username,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
        ip_address=get_client_ip(request),
    ))
    db.flush()


def _rebuild_closures(db: Session) -> None:
    folders = list(db.scalars(select(Folder)).all())
    db.execute(delete(FolderClosure))
    for folder in folders:
        current = folder
        depth = 0
        visited = set()
        while current and current.id not in visited:
            visited.add(current.id)
            db.add(FolderClosure(ancestor_id=current.id, descendant_id=folder.id, depth=depth))
            current = db.get(Folder, current.parent_id) if current.parent_id else None
            depth += 1


def _folder_payload(db: Session, folder: Folder) -> Dict[str, Any]:
    track_count = db.scalar(
        select(func.count(VFile.id)).where(
            VFile.folder_id == folder.id,
            VFile.is_trash.is_(False),
            VFile.mime_type.like("audio/%"),
        )
    ) or 0
    child_count = db.scalar(select(func.count(Folder.id)).where(Folder.parent_id == folder.id)) or 0
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "is_trash": bool(folder.is_trash),
        "is_starred": bool(folder.is_starred),
        "color": folder.color,
        "note": folder.note,
        "track_count": int(track_count),
        "child_count": int(child_count),
        "updated_at": folder.updated_at.isoformat() if folder.updated_at else None,
    }


def _file_payload(file: VFile) -> Dict[str, Any]:
    return {
        "id": file.id,
        "name": file.name,
        "folder_id": file.folder_id,
        "size": file.size,
        "mime_type": file.mime_type,
        "is_trash": bool(file.is_trash),
        "is_starred": bool(file.is_starred),
        "pending_deletion": bool(file.pending_deletion),
        "note": file.note,
        "updated_at": file.updated_at.isoformat() if file.updated_at else None,
    }


@router.post("/import/preview")
def preview_vfs_sql_import(
    request: Request,
    file: UploadFile = File(...),
    source_folder_path: str = Form("Music"),
    source_folder_id: Optional[int] = Form(None),
    target_root_id: Optional[int] = Form(None),
    include_non_audio: bool = Form(False),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_backup_admin),
):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_IMPORT_BYTES + 1024 * 1024:
                raise HTTPException(status_code=413, detail="SQL-файл превышает допустимый размер 256 МБ")
        except ValueError:
            pass
    try:
        result = preview_vfs_import(
            db,
            file,
            source_folder_path,
            source_folder_id,
            target_root_id,
            include_non_audio,
            user.id,
        )
    except VfsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    _audit(
        db,
        request,
        user,
        "vfs.import_previewed",
        "vfs_import",
        result["import_id"],
        {
            "size_bytes": result["file"]["size_bytes"],
            "source_folder": result["source"]["folder_path"],
            "summary": result["summary"],
        },
    )
    db.commit()
    return result


@router.post("/import/{import_id}/run")
def run_vfs_sql_import(
    import_id: str,
    body: VfsImportRunRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_backup_admin),
):
    _import_budget(user, "run")

    def record_audit(transaction, result):
        _audit(
            transaction,
            request,
            user,
            "vfs.import_completed",
            "vfs_import",
            import_id,
            {
                "source_folder": result["source_folder_path"],
                "folders_created": result["folders_created"],
                "folders_reused": result["folders_reused"],
                "files_imported": result["files_imported"],
                "files_conflicts": result["files_conflicts"],
            },
        )

    try:
        result = run_vfs_import(db, import_id, user.id, body.confirm, before_commit=record_audit)
    except VfsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return result


@router.get("/import/{import_id}")
def get_vfs_sql_import(
    import_id: str,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_backup_admin),
):
    try:
        return get_vfs_import(import_id, user.id)
    except VfsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/import/upload")
def upload_vfs_backup(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_backup_admin),
):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_IMPORT_BYTES + 1024 * 1024:
                raise HTTPException(status_code=413, detail="Файл превышает допустимый размер 256 МБ")
        except ValueError:
            pass
    try:
        result = stage_vfs_upload(file, user.id)
    except VfsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    _audit(
        db,
        request,
        user,
        "vfs.backup_uploaded",
        "vfs_import",
        result["import_id"],
        {
            "filename": result["filename"],
            "size_bytes": result["size_bytes"],
            "total_folders": result["total_folders"],
            "total_files": result["total_files"],
        },
    )
    db.commit()
    return result


@router.get("/import/{import_id}/children")
def get_vfs_import_children_route(
    import_id: str,
    parent_id: Optional[int] = Query(None),
    user: AppUser = Depends(get_backup_admin),
):
    try:
        return get_staged_children(import_id, user.id, parent_id)
    except VfsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/import/{import_id}/tree-preview")
def preview_vfs_tree_import_route(
    import_id: str,
    body: VfsTreePreviewRequest,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_backup_admin),
):
    try:
        return preview_tree_import(
            db,
            import_id,
            user.id,
            body.selected_folder_ids,
            body.selected_file_ids,
            body.target_root_id,
            preserve_parent_path=body.preserve_parent_path,
            catalog_kind=body.catalog_kind,
        )
    except VfsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.post("/import/{import_id}/tree-run")
def run_vfs_tree_import_route(
    import_id: str,
    body: VfsTreeRunRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_backup_admin),
):
    _import_budget(user, "tree-run")

    def record_audit(transaction, result):
        _audit(
            transaction,
            request,
            user,
            "vfs.tree_import_completed",
            "vfs_import",
            import_id,
            {
                "folders_created": result["folders_created"],
                "folders_reused": result["folders_reused"],
                "files_imported": result["files_imported"],
                "files_skipped": result["files_skipped"],
            },
        )

    try:
        result = run_tree_import(
            db,
            import_id,
            user.id,
            body.selected_folder_ids,
            body.selected_file_ids,
            body.target_root_id,
            body.confirm,
            preserve_parent_path=body.preserve_parent_path,
            catalog_kind=body.catalog_kind,
            before_commit=record_audit,
        )
    except VfsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return result


@router.get("/import/{import_id}/status")
def get_vfs_import_status_route(
    import_id: str,
    user: AppUser = Depends(get_backup_admin),
):
    try:
        return get_import_status(import_id, user.id)
    except VfsImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/live-children")
async def get_live_vfs_children(
    parent_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    """Дерево текущей БД сервера для панели приёмника (симметрия с деревом бэкапа).

    Возвращает тот же shape, что и GET /import/{import_id}/children:
    folders с subfolder_count/file_count + files с is_audio.
    """
    from app.services.vfs_import_service import _is_audio as _check_audio

    if parent_id is None:
        folders = list(
            db.scalars(
                select(Folder)
                .where(
                    or_(Folder.parent_id.is_(None), Folder.parent_id == 0),
                    Folder.is_trash.is_(False),
                )
                .order_by(Folder.name)
            ).all()
        )
        files = list(
            db.scalars(
                select(VFile)
                .options(load_only(VFile.id, VFile.name, VFile.folder_id, VFile.size, VFile.mime_type))
                .where(
                    or_(VFile.folder_id.is_(None), VFile.folder_id == 0),
                    VFile.is_trash.is_(False),
                )
                .order_by(VFile.name)
            ).all()
        )
    else:
        parent = db.get(Folder, parent_id)
        if not parent or parent.is_trash:
            raise HTTPException(status_code=404, detail="Папка приёмника не найдена")
        folders = list(
            db.scalars(
                select(Folder)
                .where(Folder.parent_id == parent_id, Folder.is_trash.is_(False))
                .order_by(Folder.name)
            ).all()
        )
        files = list(
            db.scalars(
                select(VFile)
                .options(load_only(VFile.id, VFile.name, VFile.folder_id, VFile.size, VFile.mime_type))
                .where(VFile.folder_id == parent_id, VFile.is_trash.is_(False))
                .order_by(VFile.name)
            ).all()
        )

    # One grouped count per relation instead of two count queries per folder (was N+1).
    folder_ids = [f.id for f in folders]
    sub_counts = dict(
        db.execute(
            select(Folder.parent_id, func.count(Folder.id))
            .where(Folder.parent_id.in_(folder_ids), Folder.is_trash.is_(False))
            .group_by(Folder.parent_id)
        ).all()
    ) if folder_ids else {}
    file_counts = dict(
        db.execute(
            select(VFile.folder_id, func.count(VFile.id))
            .where(VFile.folder_id.in_(folder_ids), VFile.is_trash.is_(False))
            .group_by(VFile.folder_id)
        ).all()
    ) if folder_ids else {}
    folder_rows = []
    for folder in folders:
        folder_rows.append({
            "id": folder.id,
            "name": folder.name,
            "parent_id": folder.parent_id,
            "subfolder_count": int(sub_counts.get(folder.id, 0)),
            "file_count": int(file_counts.get(folder.id, 0)),
        })
    file_rows = [
        {
            "id": f.id,
            "name": f.name,
            "folder_id": f.folder_id,
            "size": f.size,
            "mime_type": f.mime_type,
            "is_audio": bool(_check_audio(f.name, f.mime_type)),
        }
        for f in files
    ]
    return {"ok": True, "parent_id": parent_id, "folders": folder_rows, "files": file_rows}


@router.get("/folders")
async def list_admin_folders(
    parent_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None, max_length=200),
    include_trash: bool = True,
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    stmt = select(Folder)
    if parent_id is not None:
        stmt = stmt.where(Folder.parent_id == parent_id)
    if not include_trash:
        stmt = stmt.where(Folder.is_trash.is_(False))
    if search and search.strip():
        stmt = stmt.where(Folder.name.ilike(f"%{search.strip()}%"))
    folders = list(db.scalars(stmt.order_by(Folder.is_trash, Folder.name).limit(limit)).all())
    return {"items": [_folder_payload(db, folder) for folder in folders]}


@router.patch("/folders/{folder_id}")
async def update_admin_folder(
    folder_id: int,
    body: FolderUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    folder = db.get(Folder, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Folder name must not be empty")
        folder.name = name
    if body.set_parent:
        if body.parent_id == folder.id:
            raise HTTPException(status_code=400, detail="A folder cannot contain itself")
        if body.parent_id is not None:
            parent = db.get(Folder, body.parent_id)
            if not parent or parent.is_trash:
                raise HTTPException(status_code=404, detail="Parent folder not found")
            cursor = parent
            visited = set()
            while cursor and cursor.id not in visited:
                if cursor.id == folder.id:
                    raise HTTPException(status_code=400, detail="Folder move would create a cycle")
                visited.add(cursor.id)
                cursor = db.get(Folder, cursor.parent_id) if cursor.parent_id else None
        folder.parent_id = body.parent_id
    if body.is_trash is not None:
        folder.is_trash = body.is_trash
    if body.is_starred is not None:
        folder.is_starred = body.is_starred
    if "color" in body.model_fields_set:
        folder.color = body.color
    if "note" in body.model_fields_set:
        folder.note = body.note
    folder.updated_at = utcnow()
    if body.set_parent:
        _rebuild_closures(db)
    _audit(db, request, user, "vfs.folder_updated", "folder", folder.id, body.model_dump(exclude_unset=True))
    db.commit()
    return _folder_payload(db, folder)


@router.get("/files")
async def list_admin_files(
    search: Optional[str] = Query(None, max_length=300),
    folder_id: Optional[int] = Query(None),
    include_trash: bool = True,
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    stmt = select(VFile).where(VFile.mime_type.like("audio/%"))
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(or_(VFile.name.ilike(pattern), VFile.note.ilike(pattern)))
    if folder_id is not None:
        stmt = stmt.where(VFile.folder_id == folder_id)
    if not include_trash:
        stmt = stmt.where(VFile.is_trash.is_(False))
    files = list(db.scalars(stmt.order_by(VFile.name).offset(offset).limit(limit)).all())
    return {"items": [_file_payload(file) for file in files]}


@router.patch("/files/{file_id}")
async def update_admin_file(
    file_id: int,
    body: FileUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    file = db.get(VFile, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    if body.set_folder:
        if body.folder_id is not None:
            folder = db.get(Folder, body.folder_id)
            if not folder or folder.is_trash:
                raise HTTPException(status_code=404, detail="Target folder not found")
        file.folder_id = body.folder_id
    if body.is_trash is not None:
        file.is_trash = body.is_trash
    if body.is_starred is not None:
        file.is_starred = body.is_starred
    if body.pending_deletion is not None:
        file.pending_deletion = body.pending_deletion
    if "note" in body.model_fields_set:
        file.note = body.note
    file.updated_at = utcnow()
    _audit(db, request, user, "vfs.file_updated", "file", file.id, body.model_dump(exclude_unset=True))
    db.commit()
    return _file_payload(file)


@router.post("/files/move")
async def move_admin_files(
    body: FileMove,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    folder = db.get(Folder, body.folder_id)
    if not folder or folder.is_trash:
        raise HTTPException(status_code=404, detail="Target folder not found")
    files = list(db.scalars(select(VFile).where(VFile.id.in_(body.file_ids))).all())
    if len(files) != len(set(body.file_ids)):
        raise HTTPException(status_code=404, detail="One or more files were not found")
    for file in files:
        file.folder_id = folder.id
        file.updated_at = utcnow()
    _audit(db, request, user, "vfs.files_moved", "folder", folder.id, {"file_ids": body.file_ids})
    db.commit()
    return {"ok": True, "moved": len(files), "folder_id": folder.id}


@router.post("/folders/rebuild-closures")
async def rebuild_vfs_closures(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    _rebuild_closures(db)
    _audit(db, request, user, "vfs.closures_rebuilt", "vfs", "all")
    db.commit()
    return {"ok": True}
