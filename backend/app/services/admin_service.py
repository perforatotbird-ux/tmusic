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

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import engine
from app.models.auth import AppSetting
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
    row = db.get(AppSetting, key)
    if row is not None and row.value is not None:
        return row.value
    if default is not None:
        return default
    return DEFAULT_SETTINGS.get(key)


def set_setting(db: Session, key: str, value: str) -> AppSetting:
    if key not in ALLOWED_SETTING_KEYS:
        raise ValueError(f"Unknown setting key: {key}")
    row = db.get(AppSetting, key)
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
    for row in db.scalars(select(AppSetting)).all():
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


def run_pg_dump(db: Session, reason: str = "manual") -> Dict[str, Any]:
    """Create a custom-format pg_dump in the configured backup directory."""
    cfg = get_all_settings(db)
    bdir = ensure_backup_dir(Path(cfg["backup_dir"]))
    info = parse_database_url(settings.DATABASE_URL)
    if not info:
        return {"ok": False, "error": "DATABASE_URL is not a parseable PostgreSQL URL"}

    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    dump_name = f"tgmusic-{stamp}.dump"
    dump_path = bdir / dump_name
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
            timeout=30 * 60,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "pg_dump not found in PATH on this host"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "pg_dump timed out after 30 minutes"}

    if proc.returncode != 0 or not dump_path.exists():
        err = (proc.stderr or proc.stdout or "").strip()[:500]
        return {"ok": False, "error": f"pg_dump failed ({proc.returncode}): {err}"}

    removed = _prune_old_backups(bdir, int(cfg["backup_retention"]))
    size = dump_path.stat().st_size
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
    if not str(path).startswith(str(bdir.resolve())):
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
    if not str(path).startswith(str(bdir.resolve())) or not path.is_file():
        raise FileNotFoundError(name)
    return path


# ─── DB maintenance ──────────────────────────────────────────────────────────

def db_table_stats(db: Session) -> List[Dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT relname AS table,
                   n_live_tup AS live_rows,
                   n_dead_tup AS dead_rows,
                   pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                   last_vacuum,
                   last_autovacuum,
                   last_analyze,
                   last_autoanalyze
            FROM pg_stat_user_tables
            ORDER BY n_live_tup DESC
            LIMIT 100
            """
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def db_overview(db: Session) -> Dict[str, Any]:
    size = db.execute(text("SELECT pg_size_pretty(pg_database_size(current_database())) AS s")).scalar()
    conns = db.execute(text("SELECT count(*) FROM pg_stat_activity")).scalar()
    max_conn = db.execute(text("SHOW max_connections")).scalar()
    version = db.execute(text("SELECT version()")).scalar()
    uptime = db.execute(text("SELECT now() - pg_postmaster_start_time() AS up")).scalar()
    tables = db.execute(
        text(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
    ).scalar()
    db_size_bytes = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
    return {
        "database": settings.DATABASE_URL.rsplit("/", 1)[-1] if settings.DATABASE_URL else None,
        "db_size": size,
        "db_size_bytes": int(db_size_bytes or 0),
        "connections": int(conns or 0),
        "max_connections": max_conn,
        "tables": int(tables or 0),
        "server_version": version,
        "uptime": str(uptime) if uptime is not None else None,
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
