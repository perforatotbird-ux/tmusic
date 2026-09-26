"""
T-Music Admin API
Database maintenance, scheduled backups, settings, users/roles.
Access: JWT session of user with role=admin (or username admin).
"""
import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_admin_user
from app.core.config import settings
from app.core.database import get_db
from app.models.auth import AdminAuditEvent, AppUser, RevokedToken
from app.models.music import MusicAlbum, MusicArtist
from app.models.playlist import Playlist
from app.models.playback import ListeningHistory, PlaybackPosition, UserFavorite
from app.models.share import ShareLink
from app.models.vfs import TGAccount, VFile
from app.services import admin_service
from app.services.ram_cache_service import ram_cache
from app.core.disk_cache import external_cache
from app.tg.manager import tg_manager
from app.utils.time import utcnow
from app.api.dependencies import user_is_admin
from app.core.user_cache import invalidate_user
from app.utils.client_ip import get_client_ip

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


class RestoreRequest(BaseModel):
    confirm: Optional[str] = Field(default=None, max_length=160)
    dry_run: bool = True


class UserPatch(BaseModel):
    role: Optional[str] = Field(default=None, pattern="^(admin|editor|user)$")
    is_active: Optional[bool] = None
    new_password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class CreateUser(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="user", pattern="^(admin|editor|user)$")


def _audit(
    db: Session,
    request: Request,
    actor: AppUser,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    event = AdminAuditEvent(
        actor_id=actor.id,
        actor_username=actor.username,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
        ip_address=get_client_ip(request),
    )
    db.add(event)
    db.flush()


def _active_admin_count(db: Session) -> int:
    return db.scalar(
        select(func.count(AppUser.id)).where(
            AppUser.is_active.is_(True),
            (AppUser.role == "admin") | (AppUser.username == "admin"),
        )
    ) or 0


def _user_data_counts(db: Session, user_id: int) -> Dict[str, int]:
    return {
        "playlists": db.scalar(select(func.count(Playlist.id)).where(Playlist.user_id == user_id)) or 0,
        "history": db.scalar(select(func.count(ListeningHistory.id)).where(ListeningHistory.user_id == user_id)) or 0,
        "favorites": db.scalar(select(func.count(UserFavorite.id)).where(UserFavorite.user_id == user_id)) or 0,
        "positions": db.scalar(select(func.count(PlaybackPosition.id)).where(PlaybackPosition.user_id == user_id)) or 0,
        "shares": db.scalar(select(func.count(ShareLink.id)).where(ShareLink.user_id == user_id)) or 0,
    }


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
        "external_cache": external_cache.stats(),
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
    return {
        "items": admin_service.list_backups(db),
        "runs": admin_service.list_backup_runs(db),
        "settings": admin_service.get_all_settings(db),
    }


@router.post("/backups/run")
async def admin_run_backup(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    result = admin_service.run_pg_dump(db, reason=f"manual:{user.username}", actor_id=user.id)
    logger.info("Admin %s backup: %s", user.username, result)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error") or "Backup failed")
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


@router.post("/backups/{name}/restore")
async def admin_restore_backup(
    name: str,
    body: RestoreRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    try:
        result = admin_service.restore_backup(
            db,
            name,
            confirm=body.confirm,
            dry_run=body.dry_run,
            actor_id=user.id,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Backup not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    _audit(db, request, user, "backup.restore_preview" if body.dry_run else "backup.restored", "backup", name, result)
    db.commit()
    return result


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
            "token_version": u.token_version or 0,
            "has_subsonic_token": bool(u.subsonic_token_enc),
        }
        for u in users
    ]


@router.post("/users")
async def admin_create_user(
    body: CreateUser,
    request: Request,
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
    db.flush()
    _audit(db, request, user, "user.created", "user", new_user.id, {"username": new_user.username, "role": new_user.role})
    db.commit()
    db.refresh(new_user)
    logger.info("Admin %s created user %s role=%s", user.username, new_user.username, new_user.role)
    return {"id": new_user.id, "username": new_user.username, "role": new_user.role}


@router.patch("/users/{user_id}")
async def admin_patch_user(
    user_id: int,
    body: UserPatch,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    target = db.scalar(select(AppUser).where(AppUser.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == user.id and (body.role is not None or body.is_active is False):
        raise HTTPException(status_code=400, detail="Cannot demote or disable your own account")
    if body.role is not None and body.role != target.role:
        if user_is_admin(target) and body.role != "admin" and _active_admin_count(db) <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last active administrator")
        target.role = body.role
    if body.is_active is not None and body.is_active != target.is_active:
        if target.is_active and not body.is_active and user_is_admin(target) and _active_admin_count(db) <= 1:
            raise HTTPException(status_code=400, detail="Cannot disable the last active administrator")
        target.is_active = body.is_active
    if body.new_password:
        from app.core.security import hash_password

        target.password_hash = hash_password(body.new_password)
        target.token_version = (target.token_version or 0) + 1
        target.failed_login_attempts = 0
        target.locked_until = None
    _audit(
        db,
        request,
        user,
        "user.updated",
        "user",
        target.id,
        {
            "username": target.username,
            "role": target.role,
            "is_active": target.is_active,
            "password_changed": bool(body.new_password),
        },
    )
    db.commit()
    invalidate_user(target.username)
    logger.info("Admin %s patched user %s", user.username, target.username)
    return {
        "id": target.id,
        "username": target.username,
        "role": getattr(target, "role", "user"),
        "is_active": target.is_active,
    }


@router.get("/audit")
async def admin_list_audit(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: Optional[str] = Query(None, max_length=120),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    query = select(AdminAuditEvent).order_by(AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc())
    if action:
        query = query.where(AdminAuditEvent.action == action)
    events = db.scalars(query.offset(offset).limit(limit)).all()
    return {
        "items": [
            {
                "id": event.id,
                "actor": event.actor_username,
                "action": event.action,
                "target_type": event.target_type,
                "target_id": event.target_id,
                "metadata": json.loads(event.metadata_json) if event.metadata_json else {},
                "ip": event.ip_address,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in events
        ],
        "total": db.scalar(select(func.count(AdminAuditEvent.id)).where(AdminAuditEvent.action == action) if action else select(func.count(AdminAuditEvent.id))),
    }


@router.post("/users/{user_id}/unlock")
async def admin_unlock_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    target = db.scalar(select(AppUser).where(AppUser.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target.failed_login_attempts = 0
    target.locked_until = None
    _audit(db, request, user, "user.unlocked", "user", target.id, {"username": target.username})
    db.commit()
    invalidate_user(target.username)
    return {"ok": True, "username": target.username}


@router.post("/users/{user_id}/revoke-sessions")
async def admin_revoke_user_sessions(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    target = db.scalar(select(AppUser).where(AppUser.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target.token_version = (target.token_version or 0) + 1
    _audit(db, request, user, "user.sessions_revoked", "user", target.id, {"username": target.username})
    db.commit()
    invalidate_user(target.username)
    return {"ok": True, "username": target.username}


@router.delete("/users/{user_id}")
async def admin_delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    target = db.scalar(select(AppUser).where(AppUser.id == user_id))
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    if user_is_admin(target) and _active_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last active administrator")
    counts = _user_data_counts(db, user_id)
    if any(counts.values()):
        raise HTTPException(status_code=409, detail={"message": "User has data; deactivate instead", "counts": counts})
    _audit(db, request, user, "user.deleted", "user", target.id, {"username": target.username})
    db.delete(target)
    db.commit()
    invalidate_user(target.username)
    return {"ok": True}


@router.get("/analytics")
async def admin_analytics(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    since = utcnow() - timedelta(days=days)
    totals = db.execute(
        select(
            func.count(ListeningHistory.id),
            func.count(func.distinct(ListeningHistory.user_id)),
            func.count(func.distinct(ListeningHistory.file_id)),
            func.coalesce(func.sum(ListeningHistory.duration_played_sec), 0.0),
            func.coalesce(func.sum(case((ListeningHistory.completed.is_(True), 1), else_=0)), 0),
        ).where(ListeningHistory.started_at >= since)
    ).one()
    top_tracks = db.execute(
        select(
            VFile.id,
            VFile.name,
            func.count(ListeningHistory.id).label("plays"),
            func.coalesce(func.sum(ListeningHistory.duration_played_sec), 0.0).label("seconds"),
        )
        .join(VFile, VFile.id == ListeningHistory.file_id)
        .where(ListeningHistory.started_at >= since)
        .group_by(VFile.id, VFile.name)
        .order_by(func.count(ListeningHistory.id).desc())
        .limit(20)
    ).mappings().all()
    top_users = db.execute(
        select(
            AppUser.id,
            AppUser.username,
            func.count(ListeningHistory.id).label("plays"),
            func.coalesce(func.sum(ListeningHistory.duration_played_sec), 0.0).label("seconds"),
        )
        .join(ListeningHistory, ListeningHistory.user_id == AppUser.id)
        .where(ListeningHistory.started_at >= since)
        .group_by(AppUser.id, AppUser.username)
        .order_by(func.count(ListeningHistory.id).desc())
        .limit(20)
    ).mappings().all()
    daily = db.execute(
        select(
            func.date(ListeningHistory.started_at).label("day"),
            func.count(ListeningHistory.id).label("plays"),
            func.coalesce(func.sum(ListeningHistory.duration_played_sec), 0.0).label("seconds"),
        )
        .where(ListeningHistory.started_at >= since)
        .group_by(func.date(ListeningHistory.started_at))
        .order_by(func.date(ListeningHistory.started_at))
    ).mappings().all()
    return {
        "days": days,
        "totals": {
            "plays": int(totals[0] or 0),
            "listeners": int(totals[1] or 0),
            "tracks": int(totals[2] or 0),
            "seconds": float(totals[3] or 0.0),
            "completed": int(totals[4] or 0),
        },
        "top_tracks": [dict(row) for row in top_tracks],
        "top_users": [dict(row) for row in top_users],
        "daily": [dict(row) for row in daily],
    }


@router.get("/cache/stats")
async def admin_cache_stats(
    user: AppUser = Depends(get_admin_user),
):
    """Size snapshot of the caches the admin panel can act on."""
    return {
        "ram_cache": ram_cache.stats(),
        "external_cache": external_cache.stats(),
    }


@router.post("/cache/flush")
async def admin_cache_flush(
    request: Request,
    kind: str = Query("external", description="external | ram | all"),
    namespace: Optional[str] = Query(None, description="External cache namespace: lyrics | lastfm"),
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    """Drop external provider cache and/or the RAM audio buffer.

    `namespace` limits an external flush to one provider (e.g. only lyrics).
    Cover disk cache is permanent by design and is not flushable here.
    """
    kind = (kind or "external").lower()
    if kind not in ("external", "ram", "all"):
        raise HTTPException(status_code=400, detail="kind must be external | ram | all.")
    if namespace and namespace not in ("lyrics", "lastfm"):
        raise HTTPException(status_code=400, detail="namespace must be lyrics | lastfm.")

    result: Dict[str, Any] = {}
    if kind in ("external", "all"):
        result["external_removed"] = external_cache.clear(namespace)
    if kind in ("ram", "all"):
        ram_cache._cache.clear()
        ram_cache._current_bytes = 0
        result["ram_removed"] = True
    _audit(db, request, user, "cache.flushed", "cache", kind, result)
    db.commit()
    return {"status": "ok", **result}


@router.get("/system/health")
async def admin_system_health(
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    try:
        database = admin_service.db_overview(db)
    except Exception as exc:
        database = {"reachable": False, "error": str(exc)}
    return {
        "database": database,
        "telegram": tg_manager.cluster_status(),
        "ram_cache": ram_cache.stats(),
        "external_cache": external_cache.stats(),
        "backup_runs": admin_service.list_backup_runs(db, limit=10),
        "settings": admin_service.get_all_settings(db),
    }


@router.post("/tg/reconnect")
async def admin_tg_reconnect(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(get_admin_user),
):
    result = await tg_manager.reconnect()
    _audit(db, request, user, "telegram.reconnected", "telegram", None, result)
    db.commit()
    return result


@router.get("/tg/cluster")
async def admin_tg_cluster(
    user: AppUser = Depends(get_admin_user),
):
    return tg_manager.cluster_status()
