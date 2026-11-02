"""A second worker must be refused while state is process-local (SEC-010)."""
from types import SimpleNamespace

import pytest

from app import main
from app.core.config import settings


class _FakeConn:
    def __init__(self, acquired):
        self._acquired = acquired
        self.closed = False

    def execute(self, *_args, **_kwargs):
        return SimpleNamespace(scalar=lambda: self._acquired)

    def close(self):
        self.closed = True


class _FakeEngine:
    def __init__(self, acquired):
        self.dialect = SimpleNamespace(name="postgresql")
        self._conn = _FakeConn(acquired)

    def connect(self):
        return self._conn


def test_lock_is_skipped_when_multi_worker_is_acknowledged(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_MULTI_WORKER", True)
    assert main.acquire_single_instance_lock() is None


def test_second_worker_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_MULTI_WORKER", False)
    fake = _FakeEngine(acquired=False)
    monkeypatch.setattr(main, "engine", fake)
    with pytest.raises(RuntimeError, match="single-instance lock"):
        main.acquire_single_instance_lock()
    assert fake._conn.closed


def test_lock_is_held_on_first_worker(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_MULTI_WORKER", False)
    fake = _FakeEngine(acquired=True)
    monkeypatch.setattr(main, "engine", fake)
    conn = main.acquire_single_instance_lock()
    assert conn is fake._conn
    assert not conn.closed
