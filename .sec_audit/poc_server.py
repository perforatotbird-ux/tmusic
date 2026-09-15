"""PoC server: minimal reproduction of T-Music serve_spa on port 8931."""
import threading
import time
from pathlib import Path
from tempfile import mkdtemp

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

ROOT = Path(mkdtemp(prefix="tmusic_poc2_"))
DIST = ROOT / "frontend" / "dist"
(DIST / "assets").mkdir(parents=True)
(ROOT / "music.env").write_text("SECRET_KEY=REAL_SECRET_EXPOSED\n", encoding="utf-8")
(DIST / "index.html").write_text("<html>SPA</html>", encoding="utf-8")
print("ROOT:", ROOT)

app = FastAPI()
app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = DIST / full_path
    if file_path.is_file():
        return FileResponse(file_path)
    return FileResponse(DIST / "index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8931, log_level="error")
