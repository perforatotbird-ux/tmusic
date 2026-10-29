"""Telegram cluster must retry accounts that were offline at boot (REL-001)."""
import asyncio
from types import SimpleNamespace

import app.tg.manager as manager_module
from app.tg.manager import TelegramManager


class _FakeSession:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def scalars(self, _stmt):
        account = SimpleNamespace(
            id=2,
            name="second",
            api_id="123",
            api_hash="encrypted-hash",
            session_string="encrypted-session",
            is_primary=False,
        )
        return SimpleNamespace(all=lambda: [account])

    def commit(self):
        pass


def test_start_fills_missing_accounts_when_cluster_is_already_up(monkeypatch):
    monkeypatch.setattr(manager_module, "Session", _FakeSession)
    monkeypatch.setattr(manager_module, "decrypt_session_string", lambda value, _secret: value)
    monkeypatch.setattr(manager_module, "seal_tg_account", lambda _acc, _secret: 0)

    cluster = TelegramManager()
    cluster.clients = {1: object()}
    cluster.primary_id = 1
    cluster._is_started = True

    async def fake_start_single(account_id, **_kwargs):
        cluster.clients[account_id] = object()
        return cluster.clients[account_id]

    monkeypatch.setattr(cluster, "_start_single_client", fake_start_single)

    asyncio.run(cluster.start())

    assert 2 in cluster.clients


def test_keepalive_repeats_start(monkeypatch):
    cluster = TelegramManager()
    calls = {"n": 0}

    async def fake_start():
        calls["n"] += 1

    monkeypatch.setattr(cluster, "start", fake_start)

    async def run():
        cluster.start_keepalive(interval_sec=0.01)
        await asyncio.sleep(0.05)
        await cluster.stop_keepalive()

    asyncio.run(run())
    assert calls["n"] >= 1
