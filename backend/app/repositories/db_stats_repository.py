"""
T-Music DB-stats Repository — PostgreSQL introspection (raw SQL).

The only place that speaks pg_catalog/pg_stat_* directly; kept out of the
service so DB-vendor specifics stay in the data-access layer.
"""
from typing import Any, Dict, List

from sqlalchemy import text
from sqlalchemy.orm import Session


class DbStatsRepository:
    @staticmethod
    def table_stats(db: Session) -> List[Dict[str, Any]]:
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

    @staticmethod
    def overview(db: Session) -> Dict[str, Any]:
        return {
            "db_size": db.execute(
                text("SELECT pg_size_pretty(pg_database_size(current_database())) AS s")
            ).scalar(),
            "connections": db.execute(text("SELECT count(*) FROM pg_stat_activity")).scalar(),
            "max_connections": db.execute(text("SHOW max_connections")).scalar(),
            "version": db.execute(text("SELECT version()")).scalar(),
            "uptime": db.execute(text("SELECT now() - pg_postmaster_start_time() AS up")).scalar(),
            "tables": db.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            ).scalar(),
            "db_size_bytes": db.execute(
                text("SELECT pg_database_size(current_database())")
            ).scalar(),
        }
