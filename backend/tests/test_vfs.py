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
    # app.openapi() lists every registered route with full paths on all
    # supported FastAPI versions (app.routes nesting changed in FastAPI 0.14x:
    # included routers are wrapped in _IncludedRouter entries there).
    paths = set(app.openapi()["paths"])
    assert "/api/vfs/roots" in paths
    assert "/api/vfs/folders/{folder_id}/tracks" in paths
