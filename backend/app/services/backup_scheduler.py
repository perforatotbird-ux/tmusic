"""
Scheduled PostgreSQL backups (admin panel config).
"""
import asyncio
import logging
import time
import anyio
from typing import Optional

from app.core.database import SessionLocal
from app.services import admin_service

logger = logging.getLogger("tmusic.admin.scheduler")

_task: Optional[asyncio.Task] = None
_stop = asyncio.Event()


def _backup_tick(last_run_ts: float) -> float:
    """The worker owns its DB session; never pass one across threads."""
    from datetime import datetime, timedelta
    with SessionLocal() as db:
        cfg = admin_service.get_all_settings(db)
        if not cfg.get("backup_enabled"):
            return last_run_ts
        interval_h = int(cfg.get("backup_interval_hours") or 24)
        now = time.monotonic()
        if last_run_ts and now - last_run_ts < interval_h * 3600:
            return last_run_ts
        backups = admin_service.list_backups(db)
        if backups:
            try:
                latest = datetime.fromisoformat(backups[0]["created_at"])
                if datetime.now() - latest < timedelta(hours=interval_h):
                    return now
            except (ValueError, KeyError, TypeError):
                pass
        result = admin_service.run_pg_dump(db, reason="schedule")
        if result.get("ok"):
            logger.info("Scheduled backup ok: %s", result.get("name"))
        else:
            logger.warning("Scheduled backup failed: %s", result.get("error"))
        return now


async def _backup_loop():
    last_run_ts = 0.0
    while not _stop.is_set():
        try:
            await asyncio.wait_for(_stop.wait(), timeout=60)
            break
        except asyncio.TimeoutError:
            pass
        try:
            # run_sync defaults to waiting for the worker even when cancelled.
            last_run_ts = await anyio.to_thread.run_sync(_backup_tick, last_run_ts)
        except Exception:
            logger.exception("Backup scheduler iteration failed")


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
            await _task
        except asyncio.CancelledError:
            raise
        _task = None
    logger.info("Backup scheduler stopped")
