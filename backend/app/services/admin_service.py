"""
T-Music admin settings + PostgreSQL backup service.
"""
import logging
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import engine
from app.models.auth import AppSetting, BackupRun
from app.repositories.backup_run_repository import BackupRunRepository
from app.repositories.db_stats_repository import DbStatsRepository
from app.repositories.settings_repository import SettingsRepository
from app.utils.time import utcnow

logger = logging.getLogger("tmusic.admin.backup")

DEFAULT_BACKUP_DIR = settings.BASE_DIR / "data" / "backups"

DEFAULT_SETTINGS: Dict[str, str] = {
    "backup_enabled": "true",
    "backup_interval_hours": "24",
    "backup_retention": "14",
    "backup_dir": str(DEFAULT_BACKUP_DIR),
    "backup_include_schema": "true",
    "site_title": settings.PROJECT_NAME,
    "maintenance_mode": "false",
}

# Allow only safe setting keys from the admin API
ALLOWED_SETTING_KEYS = frozenset(DEFAULT_SETTINGS.keys())


def ensure_backup_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = SettingsRepository.get(db, key)
    if row is not None and row.value is not None:
        return row.value
    if default is not None:
        return default
    return DEFAULT_SETTINGS.get(key)


def set_setting(db: Session, key: str, value: str) -> AppSetting:
    if key not in ALLOWED_SETTING_KEYS:
        raise ValueError(f"Unknown setting key: {key}")
    row = SettingsRepository.get(db, key)
    if row is None:
        row = AppSetting(key=key, value=str(value))
        db.add(row)
    else:
        row.value = str(value)
        row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def get_all_settings(db: Session) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(DEFAULT_SETTINGS)
    for row in SettingsRepository.list_all(db):
        if row.key in ALLOWED_SETTING_KEYS:
            out[row.key] = row.value
    # typed convenience
    out["backup_enabled"] = str(out.get("backup_enabled", "true")).lower() in ("1", "true", "yes", "on")
    try:
        out["backup_interval_hours"] = max(1, int(out.get("backup_interval_hours") or 24))
    except ValueError:
        out["backup_interval_hours"] = 24
    try:
        out["backup_retention"] = max(1, int(out.get("backup_retention") or 14))
    except ValueError:
        out["backup_retention"] = 14
    out["maintenance_mode"] = str(out.get("maintenance_mode", "false")).lower() in ("1", "true", "yes", "on")
    return out


def parse_database_url(url: str) -> Optional[Dict[str, str]]:
    m = re.match(
        r"^postgresql(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+)(?::(\d+))?/(\w+)",
        url or "",
    )
    if not m:
        return None
    return {
        "user": m.group(1),
        "password": m.group(2),
        "host": m.group(3),
        "port": m.group(4) or "5432",
        "dbname": m.group(5),
    }


def list_backups(db: Session) -> List[Dict[str, Any]]:
    cfg = get_all_settings(db)
    bdir = Path(cfg["backup_dir"])
    if not bdir.exists():
        return []
    items = []
    for p in sorted(bdir.glob("*.dump"), reverse=True) + sorted(bdir.glob("*.zip"), reverse=True):
        try:
            st = p.stat()
            items.append(
                {
                    "name": p.name,
                    "path": str(p),
                    "size_bytes": st.st_size,
                    "created_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                    "kind": p.suffix.lstrip("."),
                }
            )
        except OSError:
            continue
    # dedupe by name
    seen = set()
    uniq = []
    for it in items:
        if it["name"] in seen:
            continue
        seen.add(it["name"])
        uniq.append(it)
    uniq.sort(key=lambda x: x["created_at"], reverse=True)
    return uniq


def _prune_old_backups(bdir: Path, retention: int) -> int:
    files = sorted(bdir.glob("tgmusic-*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in files[retention:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def _start_backup_run(
    db: Session,
    kind: str,
    reason: str,
    name: Optional[str] = None,
    actor_id: Optional[int] = None,
) -> BackupRun:
    run = BackupRun(kind=kind, name=name, reason=reason, actor_id=actor_id, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _finish_backup_run(
    db: Session,
    run: BackupRun,
    status: str,
    size_bytes: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    run.status = status
    run.size_bytes = size_bytes
    if error:
        logger.error("Backup operation failed: %s", error)
    run.error = "Backup operation failed. See server logs." if error else None
    run.finished_at = utcnow()
    db.commit()


def list_backup_runs(db: Session, limit: int = 50) -> List[Dict[str, Any]]:
    rows = BackupRunRepository.list_recent(db, limit)
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "name": row.name,
            "status": row.status,
            "reason": row.reason,
            "size_bytes": row.size_bytes,
            "error": "Backup operation failed. See server logs." if row.error else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }
        for row in rows
    ]


def run_pg_dump(db: Session, reason: str = "manual", actor_id: Optional[int] = None) -> Dict[str, Any]:
    cfg = get_all_settings(db)
    bdir = ensure_backup_dir(Path(cfg["backup_dir"]))
    run = _start_backup_run(db, "dump", reason, actor_id=actor_id)
    info = parse_database_url(settings.DATABASE_URL)
    if not info:
        error = "DATABASE_URL is not a parseable PostgreSQL URL"
        _finish_backup_run(db, run, "failed", error=error)
        return {"ok": False, "error": "Backup operation failed. See server logs."}

    stamp = utcnow().strftime("%Y%m%d-%H%M%S-%f")
    dump_name = f"tgmusic-{stamp}.dump"
    dump_path = bdir / dump_name
    run.name = dump_name
    db.commit()
    env = os.environ.copy()
    env["PGPASSWORD"] = info["password"]

    cmd = [
        "pg_dump",
        "-h", info["host"],
        "-p", info["port"],
        "-U", info["user"],
        "-d", info["dbname"],
        "-Fc",
        "-f", str(dump_path),
    ]
    if str(cfg.get("backup_include_schema", "true")).lower() not in ("1", "true", "yes", "on"):
        cmd.extend(["--data-only"])

    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30 * 60,
        )
    except FileNotFoundError:
        error = "pg_dump not found in PATH on this host"
        _finish_backup_run(db, run, "failed", error=error)
        return {"ok": False, "error": "Backup operation failed. See server logs."}
    except subprocess.TimeoutExpired:
        error = "pg_dump timed out after 30 minutes"
        _finish_backup_run(db, run, "failed", error=error)
        return {"ok": False, "error": "Backup operation failed. See server logs."}

    if proc.returncode != 0 or not dump_path.exists():
        error = f"pg_dump failed ({proc.returncode}): {(proc.stderr or proc.stdout or '').strip()[:500]}"
        _finish_backup_run(db, run, "failed", error=error)
        return {"ok": False, "error": "Backup operation failed. See server logs."}

    removed = _prune_old_backups(bdir, int(cfg["backup_retention"]))
    size = dump_path.stat().st_size
    _finish_backup_run(db, run, "success", size_bytes=size)
    logger.info("Backup created: %s (%s bytes) reason=%s pruned=%s", dump_name, size, reason, removed)
    return {
        "ok": True,
        "name": dump_name,
        "path": str(dump_path),
        "size_bytes": size,
        "reason": reason,
        "pruned": removed,
        "created_at": stamp,
    }


def delete_backup(db: Session, name: str) -> bool:
    if not re.match(r"^tgmusic-[\w\-.]+\.(dump|zip)$", name):
        raise ValueError("Invalid backup name")
    cfg = get_all_settings(db)
    bdir = Path(cfg["backup_dir"])
    path = (bdir / name).resolve()
    root = bdir.resolve()
    if not path.is_relative_to(root) or path.is_symlink():
        raise ValueError("Path escape")
    if not path.is_file():
        return False
    path.unlink()
    return True


def download_backup_path(db: Session, name: str) -> Path:
    if not re.match(r"^tgmusic-[\w\-.]+\.(dump|zip)$", name):
        raise ValueError("Invalid backup name")
    cfg = get_all_settings(db)
    bdir = Path(cfg["backup_dir"])
    path = (bdir / name).resolve()
    root = bdir.resolve()
    if not path.is_relative_to(root) or path.is_symlink() or not path.is_file():
        raise FileNotFoundError(name)
    return path


def inspect_backup(db: Session, name: str) -> Dict[str, Any]:
    path = download_backup_path(db, name)
    if path.suffix.lower() != ".dump":
        return {"ok": False, "error": "Only custom-format .dump files can be inspected"}
    info = parse_database_url(settings.DATABASE_URL)
    if not info:
        return {"ok": False, "error": "Backup operation failed. See server logs."}
    env = os.environ.copy()
    env["PGPASSWORD"] = info["password"]
    try:
        proc = subprocess.run(
            ["pg_restore", "--list", str(path)],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "Backup operation failed. See server logs."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Backup operation failed. See server logs."}
    output = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout or "Invalid backup").strip()[:500]
        logger.error("Backup inspection failed: %s", error)
        return {"ok": False, "error": "Backup operation failed. See server logs."}
    return {"ok": True, "name": name, "entries": len(output), "size_bytes": path.stat().st_size}


def restore_backup(
    db: Session,
    name: str,
    confirm: Optional[str],
    dry_run: bool = True,
    actor_id: Optional[int] = None,
) -> Dict[str, Any]:
    path = download_backup_path(db, name)
    if path.suffix.lower() != ".dump":
        raise ValueError("Only custom-format .dump files can be restored")
    if not dry_run and confirm != name:
        raise PermissionError("Confirmation must exactly match the backup name")
    if dry_run:
        result = inspect_backup(db, name)
        run = _start_backup_run(db, "restore", "dry-run", name, actor_id)
        _finish_backup_run(db, run, "success" if result.get("ok") else "failed", size_bytes=result.get("size_bytes"), error=result.get("error"))
        return {**result, "dry_run": True}
    info = parse_database_url(settings.DATABASE_URL)
    if not info:
        raise RuntimeError("DATABASE_URL is not a parseable PostgreSQL URL")
    run = _start_backup_run(db, "restore", "admin", name, actor_id)
    env = os.environ.copy()
    env["PGPASSWORD"] = info["password"]
    cmd = [
        "pg_restore",
        "-h", info["host"],
        "-p", info["port"],
        "-U", info["user"],
        "-d", info["dbname"],
        "--clean",
        "--if-exists",
        "--no-owner",
        "--single-transaction",
        "--exit-on-error",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60 * 60)
    except FileNotFoundError as exc:
        _finish_backup_run(db, run, "failed", error=str(exc))
        raise RuntimeError("pg_restore not found in PATH on this host")
    except subprocess.TimeoutExpired:
        _finish_backup_run(db, run, "failed", error="pg_restore timed out")
        raise RuntimeError("pg_restore timed out")
    if proc.returncode != 0:
        error = (proc.stderr or proc.stdout or "Restore failed").strip()[:1000]
        _finish_backup_run(db, run, "failed", error=error)
        raise RuntimeError(error)
    _finish_backup_run(db, run, "success", size_bytes=path.stat().st_size)
    return {"ok": True, "name": name, "dry_run": False, "size_bytes": path.stat().st_size}


# ─── DB maintenance ──────────────────────────────────────────────────────────

def db_table_stats(db: Session) -> List[Dict[str, Any]]:
    return DbStatsRepository.table_stats(db)


def db_overview(db: Session) -> Dict[str, Any]:
    raw = DbStatsRepository.overview(db)
    return {
        "database": settings.DATABASE_URL.rsplit("/", 1)[-1] if settings.DATABASE_URL else None,
        "db_size": raw["db_size"],
        "db_size_bytes": int(raw["db_size_bytes"] or 0),
        "connections": int(raw["connections"] or 0),
        "max_connections": raw["max_connections"],
        "tables": int(raw["tables"] or 0),
        "server_version": raw["version"],
        "uptime": str(raw["uptime"]) if raw["uptime"] is not None else None,
        "reachable": True,
    }


def run_vacuum(db: Session, analyze: bool = True) -> Dict[str, Any]:
    # VACUUM cannot run inside a transaction block — use autocommit connection
    stmt = "VACUUM (ANALYZE)" if analyze else "VACUUM"
    conn = engine.connect()
    try:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text(stmt))
        return {"ok": True, "action": stmt}
    finally:
        conn.close()


def run_analyze(db: Session) -> Dict[str, Any]:
    conn = engine.connect()
    try:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text("ANALYZE"))
        return {"ok": True, "action": "ANALYZE"}
    finally:
        conn.close()


def run_reindex(db: Session) -> Dict[str, Any]:
    conn = engine.connect()
    try:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        dbname = conn.execute(text("SELECT current_database()")).scalar()
        conn.execute(text(f'REINDEX DATABASE "{dbname}"'))
        return {"ok": True, "action": "REINDEX DATABASE", "database": dbname}
    finally:
        conn.close()


def ensure_indexes(db: Session) -> Dict[str, Any]:
    from app.core.database import ensure_performance_indexes, ensure_trigram_indexes, ensure_feature_tables, ensure_auth_schema

    ensure_performance_indexes()
    ensure_trigram_indexes()
    ensure_auth_schema()
    ensure_feature_tables()
    return {"ok": True, "action": "ensure schema/indexes"}


def purge_expired_tokens(db: Session) -> Dict[str, Any]:
    from app.core.token_store import purge_expired_revocations

    n = purge_expired_revocations(db) or 0
    return {"ok": True, "removed": n}
