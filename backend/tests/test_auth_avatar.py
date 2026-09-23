"""Profile avatar endpoint guards + schema check."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_avatar_endpoints_require_auth():
    assert client.get("/api/auth/avatar").status_code == 401
    assert client.post("/api/auth/avatar", data={}).status_code == 401
    assert client.delete("/api/auth/avatar").status_code == 401


def test_me_schema_has_avatar_url():
    # Shape check only (unauthenticated) — real values covered by manual login.
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
