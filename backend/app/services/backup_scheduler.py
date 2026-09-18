"""
Scheduled PostgreSQL backups (admin panel config).
"""
import asyncio
import logging
from typing import Optional

from app.core.database import SessionLocal
from app.services import admin_service

logger = logging.getLogger("tmusic.admin.scheduler")

_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


async def _backup_loop():
    """Wake every 60s; run pg_dump when interval elapsed and backups enabled."""
    last_run_ts: float = 0.0
    while not _stop.is_set():
        try:
            await asyncio.wait_for(_stop.wait(), timeout=60)
            break  # stop requested
        except asyncio.TimeoutError:
            pass

        try:
            with SessionLocal() as db:
                cfg = admin_service.get_all_settings(db)
                if not cfg.get("backup_enabled"):
                    continue
                interval_h = int(cfg.get("backup_interval_hours") or 24)
                interval_s = interval_h * 3600
                now = asyncio.get_event_loop().time()
                if last_run_ts and (now - last_run_ts) < interval_s:
                    continue
                backups = admin_service.list_backups(db)
                # If a dump already exists newer than interval, skip
                from datetime import datetime, timedelta

                if backups:
                    try:
                        latest = datetime.fromisoformat(backups[0]["created_at"])
                        if datetime.now() - latest < timedelta(hours=interval_h):
                            last_run_ts = now
                            continue
                    except Exception:
                        pass
                result = admin_service.run_pg_dump(db, reason="schedule")
                last_run_ts = now
                if result.get("ok"):
                    logger.info("Scheduled backup ok: %s", result.get("name"))
                else:
                    logger.warning("Scheduled backup failed: %s", result.get("error"))
        except Exception as e:
            logger.warning("Backup scheduler iteration failed: %s", e)


def start_backup_scheduler():
    global _task
    _stop.clear()
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_backup_loop())
    logger.info("Backup scheduler started")


async def stop_backup_scheduler():
    global _task
    _stop.set()
    if _task:
        try:
            await asyncio.wait_for(_task, timeout=5)
        except Exception:
            _task.cancel()
        _task = None
    logger.info("Backup scheduler stopped")
