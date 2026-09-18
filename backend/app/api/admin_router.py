"""
T-Music Admin API
Database maintenance, scheduled backups, settings, users/roles.
Access: JWT session of user with role=admin (or username admin).
"""
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_admin_user
from app.core.config import settings
from app.core.database import get_db
from app.models.auth import AppUser, RevokedToken
from app.models.music import MusicAlbum, MusicArtist
from app.models.playlist import Playlist
from app.models.vfs import TGAccount, VFile
from app.services import admin_service
from app.services.ram_cache_service import ram_cache
from app.tg.manager import tg_manager
from app.utils.time import utcnow
from app.api.dependencies import user_is_admin
from app.core.user_cache import invalidate_user

logger = logging.getLogger("tmusic.admin")
router = APIRouter(prefix="/api/admin", tags=["admin"])


class SettingsUpdate(BaseModel):
    backup_enabled: Optional[bool] = None
    backup_interval_hours: Optional[int] = Field(default=None, ge=1, le=24 * 30)
    backup_retention: Optional[int] = Field(default=None, ge=1, le=365)
    backup_dir: Optional[str] = Field(default=None, max_length=500)
    backup_include_schema: Optional[bool] = None
    site_title: Optional[str] = Field(default=None, max_length=120)
    maintenance_mode: Optional[bool] = None


class UserPatch(BaseModel):
    role: Optional[str] = Field(default=None, pattern="^(admin|editor|user)$")
    is_active: Optional[bool] = None
    new_password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class CreateUser(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="user", pattern="^(admin|editor|user)$")


def _library_counts(db: Session) -> Dict[str, int]:
    return {
        "artists": db.scalar(select(func.count(MusicArtist.id))) or 0,
        "albums": db.scalar(select(func.count(MusicAlbum.id))) or 0,
        "tracks": db.scalar(select(func.count(VFile.id)).where(VFile.mime_type.like("audio/%"))) or 0,
        "folders": db.scalar(select(func.count(func.distinct(VFile.folder_id)))) or 0,
        "playlists": db.scalar(select(func.count(Playlist.id))) or 0,
        "users": db.scalar(select(func.count(AppUser.id))) or 0,
        "tg_accounts": db.scalar(select(func.count(TGAccount.id))) or 0,
        "revoked_tokens": db.scalar(select(func.count(RevokedToken.id))) or 0,
    }


@router.get("/overview")
async def admin_overview(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    cfg = admin_service.get_all_settings(db)
    try:
        db_info = admin_service.db_overview(db)
    except Exception as e:
        db_info = {"reachable": False, "error": str(e)}
    try:
        cache_info = ram_cache.stats()
    except Exception:
        cache_info = {}
    backups = admin_service.list_backups(db)
    return {
        "project": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "timestamp": utcnow().isoformat(),
        "viewer": {"id": user.id, "username": user.username, "role": getattr(user, "role", "user")},
        "library": _library_counts(db),
        "database": db_info,
        "telegram": tg_manager.cluster_status(),
        "ram_cache": cache_info,
        "settings": cfg,
        "backups": {
            "count": len(backups),
            "latest": backups[0] if backups else None,
        },
        "enrichment": {
            "lastfm_metadata": settings.lastfm_metadata_ready,
            "lastfm_scrobble": settings.lastfm_scrobble_ready,
            "discogs": settings.discogs_ready,
        },
        "subsonic": {
            "path": "/rest",
            "api_version": "1.16.1",
        },
    }


@router.get("/db/stats")
async def admin_db_stats(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    return {
        "overview": admin_service.db_overview(db),
        "tables": admin_service.db_table_stats(db),
    }


@router.post("/db/vacuum")
async def admin_db_vacuum(
    analyze: bool = True,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    result = admin_service.run_vacuum(db, analyze=analyze)
    logger.info("Admin %s ran VACUUM: %s", user.username, result)
    return result


@router.post("/db/analyze")
async def admin_db_analyze(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    result = admin_service.run_analyze(db)
    logger.info("Admin %s ran ANALYZE", user.username)
    return result


@router.post("/db/reindex")
async def admin_db_reindex(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    result = admin_service.run_reindex(db)
    logger.info("Admin %s ran REINDEX: %s", user.username, result)
    return result


@router.post("/db/ensure-schema")
async def admin_db_ensure_schema(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    return admin_service.ensure_indexes(db)


@router.post("/db/purge-tokens")
async def admin_db_purge_tokens(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    return admin_service.purge_expired_tokens(db)


@router.get("/backups")
async def admin_list_backups(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    return {"items": admin_service.list_backups(db), "settings": admin_service.get_all_settings(db)}


@router.post("/backups/run")
async def admin_run_backup(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    result = admin_service.run_pg_dump(db, reason=f"manual:{user.username}")
    logger.info("Admin %s backup: %s", user.username, result)
    return result


@router.get("/backups/{name}/download")
async def admin_download_backup(
    name: str,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    try:
        path = admin_service.download_backup_path(db, name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup name")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.delete("/backups/{name}")
async def admin_delete_backup(
    name: str,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    try:
        ok = admin_service.delete_backup(db, name)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid backup name")
    if not ok:
        raise HTTPException(status_code=404, detail="Backup not found")
    logger.info("Admin %s deleted backup %s", user.username, name)
    return {"ok": True}


@router.get("/settings")
async def admin_get_settings(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    return admin_service.get_all_settings(db)


@router.put("/settings")
async def admin_put_settings(
    body: SettingsUpdate,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    payload = body.model_dump(exclude_none=True)
    for key, value in payload.items():
        if key not in admin_service.ALLOWED_SETTING_KEYS:
            continue
        if isinstance(value, bool):
            store = "true" if value else "false"
        else:
            store = str(value)
        admin_service.set_setting(db, key, store)
    logger.info("Admin %s updated settings: %s", user.username, list(payload.keys()))
    return admin_service.get_all_settings(db)


@router.get("/users")
async def admin_list_users(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    users = list(db.scalars(select(AppUser).order_by(AppUser.id)).all())
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": getattr(u, "role", "user"),
            "is_active": u.is_active,
            "failed_login_attempts": u.failed_login_attempts,
            "locked_until": u.locked_until.isoformat() if u.locked_until else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "has_subsonic_token": bool(u.subsonic_token_enc),
        }
        for u in users
    ]


@router.post("/users")
async def admin_create_user(
    body: CreateUser,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    exists = db.scalar(select(AppUser).where(AppUser.username == body.username))
    if exists:
        raise HTTPException(status_code=400, detail="Username already exists")
    from app.core.security import hash_password

    new_user = AppUser(
        username=body.username.strip(),
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    logger.info("Admin %s created user %s role=%s", user.username, new_user.username, new_user.role)
    return {"id": new_user.id, "username": new_user.username, "role": new_user.role}


@router.patch("/users/{user_id}")
async def admin_patch_user(
    user_id: int,
    body: UserPatch,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    target = db.scalar(select(AppUser).where(AppUser.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if body.role is not None:
        # Last admin guard
        if user_is_admin(target) and body.role != "admin":
            admins = db.scalar(
                select(func.count(AppUser.id)).where(
                    (AppUser.role == "admin") | (AppUser.username == "admin")
                )
            ) or 0
            if admins <= 1:
                raise HTTPException(status_code=400, detail="Cannot demote the last administrator")
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active
    if body.new_password:
        from app.core.security import hash_password

        target.password_hash = hash_password(body.new_password)
        target.token_version = (target.token_version or 0) + 1
        target.failed_login_attempts = 0
        target.locked_until = None
    db.commit()
    invalidate_user(target.username)
    logger.info("Admin %s patched user %s", user.username, target.username)
    return {
        "id": target.id,
        "username": target.username,
        "role": getattr(target, "role", "user"),
        "is_active": target.is_active,
    }


@router.get("/tg/cluster")
async def admin_tg_cluster(
    user: AppUser = Depends(get_admin_user),
):
    return tg_manager.cluster_status()
