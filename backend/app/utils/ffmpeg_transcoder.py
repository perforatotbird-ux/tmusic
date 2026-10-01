"""
T-Music FFmpeg Transcoding Utility
Handles on-the-fly transcoding of non-browser-compatible audio formats (m4a, flac, ogg, etc.)
to MP3 320kbps CBR for browser playback.

Design:
- ffmpeg/ffprobe binaries live in backend/app/utils/ffmpeg/
- Transcoded files cached on disk in TRANSCODE_CACHE_DIR (named by file_id + source mtime + size)
- Background prefetching of upcoming tracks in the playback queue
- SSE-based notifications for the frontend
"""
import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, List, Any, Set

from app.core.config import settings

logger = logging.getLogger("tmusic.transcoder")

# ─── FFmpeg binary paths ───────────────────────────────────────────────────────
# Default ships binaries inside app/utils/ffmpeg (Windows-first layout).
# 2026-09 audit (minor 8): FFMPEG_BIN_DIR env var lets deployments point at a
# system ffmpeg directory (Linux/CI/Docker images with apt-installed ffmpeg)
# instead of vendoring exe/dll payloads into the source tree. Binaries are
# validated lazily by ensure_ffmpeg_binaries().
FFMPEG_BIN_DIR: Path = Path(
    os.environ.get("FFMPEG_BIN_DIR") or (Path(__file__).parent / "ffmpeg")
)
FFMPEG_EXE = FFMPEG_BIN_DIR / "ffmpeg.exe"
FFPROBE_EXE = FFMPEG_BIN_DIR / "ffprobe.exe"


class TranscodeStatus(Enum):
    NOT_NEEDED = "not_needed"        # Format is browser-compatible (mp3)
    PENDING = "pending"              # Queued, waiting to start
    PROCESSING = "processing"        # Actively transcoding
    READY = "ready"                  # Transcoded file available
    FAILED = "failed"                # Error during transcoding
    CACHED = "cached"                # Was already transcoded and cached


@dataclass
class TranscodeResult:
    status: TranscodeStatus
    cached_path: Optional[str] = None
    cache_key: str = ""
    mp3_size: Optional[int] = None
    duration: Optional[float] = None
    message: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


# ─── File format detection ─────────────────────────────────────────────────────

# Extensions that need transcoding for browser compatibility
NEEDS_TRANSCODE_EXTENSIONS: Set[str] = set(settings.TRANSCODE_FORMATS_NEED)

# Extensions that browsers can play natively (no transcoding needed)
BROWSER_NATIVE_EXTENSIONS: Set[str] = {"mp3", "wav", "m4a", "webm", "aac"}

# Actually, m4a playback in browsers is inconsistent — some browsers need transcoded MP3.
# We treat m4a as needing transcoding since the user explicitly mentioned it.


def get_file_extension(filename: str) -> str:
    """Extract lowercase extension without dot."""
    if not filename:
        return ""
    dot_idx = filename.rfind(".")
    if dot_idx == -1:
        return ""
    return filename[dot_idx + 1:].lower()


def needs_transcoding(filename: str, mime_type: Optional[str] = None) -> bool:
    """Check if a file format needs transcoding for browser playback."""
    if not settings.TRANSCODE_ENABLED:
        return False
    ext = get_file_extension(filename)
    if ext in NEEDS_TRANSCODE_EXTENSIONS:
        return True
    # Also check mime_type
    if mime_type:
        mime_ext = mime_type.split("/")[-1].lower() if "/" in mime_type else ""
        if mime_ext in NEEDS_TRANSCODE_EXTENSIONS:
            return True
    # m4a/aac/m4a in mime type needs transcoding for browser safety
    if mime_type and ("mp4" in mime_type or "m4a" in mime_type or "aac" in mime_type):
        return True
    return False


# ─── Cache key generation ──────────────────────────────────────────────────────

def make_cache_key(file_id: int, file_name: str, file_size: int, file_mtime: float) -> str:
    """Generate a stable cache key based on file identity and content freshness."""
    raw = f"{file_id}|{file_name}|{file_size}|{file_mtime}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get_cached_transcode_path(cache_key: str) -> Optional[str]:
    """Return the path to a cached transcoded file if it exists and is valid."""
    cache_file = settings.TRANSCODE_CACHE_DIR / f"{cache_key}.mp3"
    manifest_file = settings.TRANSCODE_CACHE_DIR / f"{cache_key}.meta.json"
    
    if not cache_file.exists() or not manifest_file.exists():
        return None
    
    # Verify the cache is still valid
    try:
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest.get("status") != "ready":
            return None
        if not cache_file.exists():
            return None
        # Reject garbage transcodes (e.g. header-only MP3s from failed runs)
        if (cache_file.stat().st_size or 0) < 1024:
            return None
        if cache_file.stat().st_size != manifest.get("mp3_size"):
            return None
        return str(cache_file)
    except (OSError, json.JSONDecodeError):
        return None


def write_cache_manifest(cache_key: str, result: Dict[str, Any]) -> None:
    """Write metadata manifest for a cached transcoded file."""
    manifest_file = settings.TRANSCODE_CACHE_DIR / f"{cache_key}.meta.json"
    try:
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(result, f)
    except OSError as e:
        logger.warning("Failed to write transcode manifest for %s: %s", cache_key, e)


# ─── FFmpeg binary management ──────────────────────────────────────────────────

def ensure_ffmpeg_binaries() -> bool:
    """Verify that ffmpeg and ffprobe binaries are present."""
    if not FFMPEG_EXE.exists():
        logger.error("ffmpeg binary not found at %s", FFMPEG_EXE)
        return False
    if not FFPROBE_EXE.exists():
        logger.error("ffprobe binary not found at %s", FFPROBE_EXE)
        return False
    return True


def get_ffmpeg_command(input_path: str, output_path: str, bitrate_kbps: int = 320) -> List[str]:
    """Build ffmpeg command for transcoding to MP3 CBR 320kbps."""
    return [
        str(FFMPEG_EXE),
        "-y",                          # Overwrite output if exists
        "-i", input_path,              # Input file
        "-vn",                         # No video (strip video stream)
        "-c:a", "libmp3lame",          # Use LAME MP3 encoder
        "-b:a", f"{bitrate_kbps}k",    # Bitrate (CBR)
        "-cutoff", "20000",            # Cutoff frequency
        "-map_metadata", "0",          # Copy metadata (tags, artist, title, etc.)
        output_path,
    ]


# ─── Low-level transcode from byte stream ──────────────────────────────────────

async def transcode_from_bytes(
    input_bytes: bytes,
    output_path: str,
    bitrate_kbps: int = 320,
) -> None:
    """
    Transcode raw audio bytes to MP3 320kbps and write to output_path.
    Uses ffmpeg as a subprocess, writing input via stdin pipe.
    """
    if not ensure_ffmpeg_binaries():
        raise RuntimeError("ffmpeg binary not available")

    # Use a temp file: MP4/M4A inputs with the moov atom at the end require
    # seeking, which is impossible on a non-seekable pipe.
    import tempfile
    src_tmp = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tf:
            tf.write(input_bytes)
            src_tmp = tf.name

        cmd = [
            str(FFMPEG_EXE),
            "-y",                          # Overwrite
            "-i", src_tmp,                 # Input file (seekable)
            "-vn",                         # No video
            "-c:a", "libmp3lame",          # LAME MP3 encoder
            "-b:a", f"{bitrate_kbps}k",    # 320kbps CBR
            "-cutoff", "20000",
            "-f", "mp3",                   # Explicit format (required when writing to pipe:1)
            "pipe:1",                      # Output to stdout
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_data, stderr_data = await communicate_with_cleanup(
            proc,
            timeout=300,  # 5 minute timeout
        )
    finally:
        if src_tmp:
            try:
                Path(src_tmp).unlink(missing_ok=True)
            except OSError:
                pass

    if proc.returncode != 0:
        stderr_text = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""
        # Show the tail of stderr — the actual error is at the end,
        # after the version banner and stream info.
        raise RuntimeError(f"ffmpeg failed (code {proc.returncode}): {stderr_text[-800:]}")

    # Validate output: reject empty/garbage MP3s (e.g. header-only output)
    if len(stdout_data) < 1024:
        raise RuntimeError(f"ffmpeg produced invalid MP3 ({len(stdout_data)} bytes)")
    
    # Write the transcoded bytes to output file
    with open(output_path, "wb") as f:
        f.write(stdout_data)


def get_mp3_duration(file_path: str) -> Optional[float]:
    """Get duration of an audio file using ffprobe."""
    if not FFPROBE_EXE.exists():
        return None
    
    import subprocess
    try:
        result = subprocess.run(
            [
                str(FFPROBE_EXE),
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                file_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 0))
    except Exception:
        pass
    return None


__all__ = [
    "TranscodeStatus",
    "TranscodeResult",
    "needs_transcoding",
    "get_file_extension",
    "make_cache_key",
    "get_cached_transcode_path",
    "write_cache_manifest",
    "ensure_ffmpeg_binaries",
    "get_ffmpeg_command",
    "transcode_from_bytes",
    "get_mp3_duration",
]

async def communicate_with_cleanup(proc, timeout: float):
    """Reap the child before allowing timeout/cancellation to escape."""
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except BaseException:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        # Drain pipes as well as waiting: wait() alone can deadlock on full pipes.
        cleanup = asyncio.create_task(proc.communicate())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue  # Repeated cancellation must not abandon the child.
        cleanup.result()
        raise
