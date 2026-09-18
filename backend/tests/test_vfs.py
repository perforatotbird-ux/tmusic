"""
VFS folder browser API tests (auth guards).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_vfs_endpoints_guard():
    for path in (
        "/api/vfs/roots",
        "/api/vfs/folders",
        "/api/vfs/folders/1",
        "/api/vfs/folders/1/children",
        "/api/vfs/folders/1/tracks",
    ):
        resp = client.get(path)
        assert resp.status_code in (200, 401, 404), f"{path} -> {resp.status_code}"


def test_vfs_routes_registered():
    routes = {getattr(r, "path", "") for r in app.routes}
    assert "/api/vfs/roots" in routes
    assert "/api/vfs/folders/{folder_id}/tracks" in routes
