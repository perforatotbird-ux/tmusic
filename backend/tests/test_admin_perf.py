"""Admin panel performance guards.

Opening the panel used to take ~0.56 s of database time, almost all of it in
`pg_database_size()` — a function that walks every relation of the database and
was being evaluated twice per call. These tests pin the two fixes in place:
one evaluation per repository call, and a short-lived memo in the service so
`/overview` + `/system/health` (+ `/db/stats`) do not each pay for it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import event

from app.core.database import SessionLocal, engine
from app.repositories.db_stats_repository import DbStatsRepository
from app.services import admin_service


def test_db_overview_computes_the_database_size_exactly_once():
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with SessionLocal() as db:
            DbStatsRepository.overview(db)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    hits = [s for s in statements if "pg_database_size" in s]
    assert len(hits) == 1, f"pg_database_size must be evaluated once, got {len(hits)}: {hits}"


def test_db_overview_reports_matching_size_representations():
    with SessionLocal() as db:
        row = DbStatsRepository.overview(db)
    assert int(row["db_size_bytes"]) > 0
    assert row["db_size"] and isinstance(row["db_size"], str)


def test_db_overview_is_memoised_and_invalidatable():
    original = DbStatsRepository.overview
    calls = {"n": 0}

    def counting(db):
        calls["n"] += 1
        return original(db)

    admin_service.invalidate_db_overview()
    DbStatsRepository.overview = staticmethod(counting)
    try:
        with SessionLocal() as db:
            first = admin_service.db_overview(db)
            second = admin_service.db_overview(db)
        assert calls["n"] == 1, "the second read must come from the memo"
        assert first == second
        # Callers must not share one mutable dict across requests.
        assert first is not second

        admin_service.invalidate_db_overview()
        with SessionLocal() as db:
            admin_service.db_overview(db)
        assert calls["n"] == 2, "invalidate_db_overview() must force a fresh read"
    finally:
        DbStatsRepository.overview = staticmethod(original)
        admin_service.invalidate_db_overview()
