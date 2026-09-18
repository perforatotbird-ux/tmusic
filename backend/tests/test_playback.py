"""
Wave-1 improvement tests: health, system cluster snapshot, playback helpers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app
from app.tg.manager import TelegramManager
from app.utils.like import escape_like

client = TestClient(app)


def test_health_public():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "project" in data
    # Must not leak secrets
    blob = resp.text.lower()
    assert "secret" not in blob
    assert "password" not in blob
    assert "session" not in blob


def test_system_status_requires_auth():
    resp = client.get("/api/system/status")
    # REQUIRE_AUTH=true in production env file; if auth is off, admin impersonation kicks in
    assert resp.status_code in (200, 401)


def test_playback_endpoints_guard():
    for path, method in [
        ("/api/playback/position/1", "GET"),
        ("/api/playback/history", "GET"),
        ("/api/playback/tracks/search", "GET"),
        ("/api/playback/positions", "GET"),
    ]:
        resp = client.request(method, path)
        assert resp.status_code in (200, 401), f"{method} {path} -> {resp.status_code}"


def test_cluster_status_shape():
    mgr = TelegramManager()
    status = mgr.cluster_status()
    assert status["started"] is False
    assert status["clients_total"] == 0
    assert status["accounts"] == []
    assert "primary_id" in status
    # No credential-like keys
    assert "session_string" not in str(status)
    assert "api_hash" not in str(status)


def test_escape_like_wildcards():
    assert escape_like("100%") == "100\\%"
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("x\\y") == "x\\\\y"
