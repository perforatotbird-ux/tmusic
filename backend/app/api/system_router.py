"""
T-Music System Status API
Liveness and authenticated cluster/runtime snapshot (no secrets).
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.dependencies import get_admin_user
from app.core.config import settings
from app.core.database import engine, get_db
from app.models.auth import AppUser
from app.services import admin_service
from app.services.ram_cache_service import ram_cache
from app.tg.manager import tg_manager
from app.utils.time import utcnow

logger = logging.getLogger("tmusic.system")

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/config")
async def system_config(db: Session = Depends(get_db)):
    try:
        values = admin_service.get_all_settings(db)
    except Exception:
        logger.debug("Failed to load system settings; using defaults", exc_info=True)
        values = {"site_title": settings.PROJECT_NAME, "maintenance_mode": False}
    return {
        "site_title": values.get("site_title") or settings.PROJECT_NAME,
        "maintenance_mode": bool(values.get("maintenance_mode")),
    }


def _db_ping() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.debug("Database ping failed", exc_info=True)
        return False


@router.get("/status")
async def system_status(user: AppUser = Depends(get_admin_user)):
    """Operational snapshot (environment, DB, Telegram cluster, caches).

    SECURITY: admin-gated. It used to accept any authenticated user while its
    docstring claimed "for admins" — infrastructure topology is not something a
    regular account needs. Nothing in the web/mobile clients calls it.
    """
    tg = tg_manager.cluster_status()
    try:
        cache_info: Dict[str, Any] = ram_cache.stats()
    except Exception:
        cache_info = {"error": "unavailable"}

    return {
        "project": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
        "require_auth": settings.REQUIRE_AUTH,
        "timestamp": utcnow().isoformat(),
        "database": {"ok": _db_ping()},
        "telegram": tg,
        "ram_cache": cache_info,
        "enrichment": {
            "lastfm_metadata": settings.lastfm_metadata_ready,
            "lastfm_scrobble": settings.lastfm_scrobble_ready,
            "discogs": settings.discogs_ready,
        },
        "user": {"id": user.id, "username": user.username},
    }


@router.get("/cluster")
async def tg_cluster_status(user: AppUser = Depends(get_admin_user)):
    """Per-account Telegram cluster state — admin-only (same reasoning as /status)."""
    return tg_manager.cluster_status()
