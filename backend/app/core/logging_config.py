"""
T-Music Logging System
======================
Structured logging with file output and password-protected rotation.

Design (best practices):
- File sink: JSON Lines (one JSON object per line) — stable keys, machine/LLM
  friendly, greppable, diffable.
- Console sink: human-readable colored text for live debugging.
- Levels: DEBUG < INFO < WARNING < ERROR < CRITICAL (standard library names).
- Rotation: size-based; rolled file is compressed into an AES-256 encrypted
  ZIP (pyzipper, WinZip AES) protected by LOG_ARCHIVE_PASSWORD; the raw
  rolled file is deleted immediately; only LOG_ARCHIVE_KEEP archives are
  retained (older ones pruned).
- Streams: backend.log (server) and frontend.log (browser events ingested
  via POST /api/system/client-logs) live in LOG_DIR.
- Correlation: contextvar request_id injected into every record emitted
  during a request.
- Never-crash guarantee: any failure inside a handler/rollover is swallowed
  and reported on stderr — logging must not take the app down.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.config import settings

try:
    import pyzipper
except ImportError:  # pragma: no cover — degrade to plain rotate instead of failing boot
    pyzipper = None


# ─── Record shape (JSONL) ────────────────────────────────────────────────────
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()
) | {"message", "asctime", "taskName"}

_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",     # cyan
    logging.INFO: "\033[32m",      # green
    logging.WARNING: "\033[33m",   # yellow
    logging.ERROR: "\033[31m",     # red
    logging.CRITICAL: "\033[41m",  # red bg
}
_RESET = "\033[0m"
_DIM = "\033[2m"

_FRONTEND_LOGGER_NAME = "tmusic.frontend"
_setup_lock = threading.Lock()
_setup_done = False


def utc_iso(ts: Optional[float] = None) -> str:
    """ISO-8601 UTC with milliseconds and Z suffix — LLM/JSON friendly."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else datetime.now(timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line. Stable core keys + record extras."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": utc_iso(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", None) or _request_id.get()
        if rid:
            payload["request_id"] = rid
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key == "request_id" or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
        except (TypeError, ValueError):
            payload["msg"] = repr(payload.get("msg"))
            return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


class HumanFormatter(logging.Formatter):
    """Readable console format: time [LEVEL] logger: message (+extras)."""

    def __init__(self, use_color: bool = True):
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created)
        t = dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(record.msecs):03d}"
        level = record.levelname
        if self.use_color:
            color = _LEVEL_COLORS.get(record.levelno, "")
            level_colored = f"{color}{level:<8}{_RESET}"
            head = f"{_DIM}{t}{_RESET} {level_colored} {_DIM}{record.name}{_RESET}: {record.getMessage()}"
        else:
            head = f"{t} [{level:<8}] {record.name}: {record.getMessage()}"
        extras = []
        rid = getattr(record, "request_id", None) or _request_id.get()
        if rid:
            extras.append(f"rid={rid}")
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key in ("request_id", "exc_info", "exc_text", "stack_info") \
                    or key.startswith("_"):
                continue
            extras.append(f"{key}={value}")
        if extras:
            head += f"  ({', '.join(extras)})"
        if record.exc_info:
            head += "\n" + self.formatException(record.exc_info)
        return head


# ─── Request correlation ─────────────────────────────────────────────────────
import contextvars
_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)


def set_request_id(rid: Optional[str]):
    return _request_id.set(rid)


def reset_request_id(token) -> None:
    _request_id.reset(token)


# ─── Password-protected rotating file handler ───────────────────────────────
class PasswordZipRotatingFileHandler(logging.FileHandler):
    """Size-based rotation: rolled content → AES-encrypted ZIP, then prune.

    Archives are named `<file>.<UTC yyyymmdd-hhmmss>.zip` (lexicographic sort
    == chronological). Keeps the newest LOG_ARCHIVE_KEEP archives only.
    """

    def __init__(self, filename: str | Path, max_bytes: int, backup_count: int,
                 password: str, encoding: str = "utf-8"):
        self._max_bytes = max_bytes
        self._backup_count = max(1, backup_count)
        self._password = password
        self._path = Path(filename)
        super().__init__(str(self._path), encoding=encoding, delay=False)

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        try:
            if self.stream is None:
                return False
            return self.stream.tell() >= self._max_bytes
        except (OSError, ValueError):
            return False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with self.lock:
                if self.shouldRollover(record):
                    self._rollover()
                logging.StreamHandler.emit(self, record)
        except Exception:
            self.handleError(record)

    def _rollover(self) -> None:
        try:
            if self.stream:
                self.stream.flush()
                self.stream.close()
            self.stream = None

            if self._path.exists() and self._path.stat().st_size > 0:
                ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
                archive = self._path.with_name(f"{self._path.name}.{ts}.zip")
                # Collision within the same second (tiny max_bytes in tests).
                n = 1
                while archive.exists():
                    archive = self._path.with_name(f"{self._path.name}.{ts}-{n}.zip")
                    n += 1
                data = self._path.read_bytes()
                if pyzipper is not None and self._password:
                    with pyzipper.AESZipFile(
                        archive, "w",
                        compression=pyzipper.ZIP_DEFLATED,
                        encryption=pyzipper.WZ_AES,
                    ) as zf:
                        zf.setpassword(self._password.encode("utf-8"))
                        zf.writestr(self._path.name, data)
                else:
                    # Fallback: still compress, no encryption (pyzipper missing).
                    import gzip
                    archive = archive.with_suffix(".gz")
                    archive.write_bytes(gzip.compress(data))
                self._path.unlink(missing_ok=True)
                self._prune()
        except Exception:
            # Keep logging alive at all costs.
            print(f"[logging] rollover failed for {self._path}", file=sys.stderr)
        finally:
            try:
                self.stream = self._open()
            except Exception:
                self.stream = None

    def _prune(self) -> None:
        stem = self._path.name + "."
        archives = sorted(
            (p for p in self._path.parent.glob(stem + "*")
             if p.suffix in (".zip", ".gz") and p.name != self._path.name),
            key=lambda p: p.name,  # timestamped names → chronological
        )
        for old in archives[:-self._backup_count]:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass

    def close(self) -> None:
        with self.lock:
            try:
                if self.stream:
                    self.stream.flush()
            except Exception:
                pass
            # Do NOT super().close() in a way that loses the archive: plain close.
            try:
                if self.stream:
                    self.stream.close()
            finally:
                self.stream = None
                logging.Handler.close(self)


# ─── Setup ───────────────────────────────────────────────────────────────────
def setup_logging() -> None:
    """Idempotent. Called once from app.main before any request is served."""
    global _setup_done
    with _setup_lock:
        if _setup_done:
            return
        _setup_done = True

    log_dir: Path = Path(settings.LOG_DIR)
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[logging] cannot create LOG_DIR {log_dir}: {e}", file=sys.stderr)

    root = logging.getLogger()
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    root.setLevel(level)
    # Drop handlers installed by uvicorn/basicConfig to avoid duplicates.
    for h in list(root.handlers):
        root.removeHandler(h)

    # 1) Console — human readable, colorized only on a TTY.
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, settings.LOG_CONSOLE_LEVEL.upper(), logging.INFO))
    console.setFormatter(HumanFormatter(use_color=sys.stdout.isatty()))
    root.addHandler(console)

    # 2) Backend file — JSONL with encrypted-zip rotation.
    try:
        file_handler = PasswordZipRotatingFileHandler(
            log_dir / "backend.log",
            max_bytes=settings.LOG_MAX_BYTES,
            backup_count=settings.LOG_ARCHIVE_KEEP,
            password=settings.LOG_ARCHIVE_PASSWORD,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(JsonLineFormatter())
        root.addHandler(file_handler)
    except Exception as e:
        print(f"[logging] cannot open backend.log handler: {e}", file=sys.stderr)

    # 3) Frontend file — separate stream (browser events via ingest API).
    frontend_logger = logging.getLogger(_FRONTEND_LOGGER_NAME)
    frontend_logger.handlers.clear()
    frontend_logger.propagate = False  # must NOT double-write into backend.log
    try:
        fe_handler = PasswordZipRotatingFileHandler(
            log_dir / "frontend.log",
            max_bytes=settings.LOG_MAX_BYTES,
            backup_count=settings.LOG_ARCHIVE_KEEP,
            password=settings.LOG_ARCHIVE_PASSWORD,
        )
        fe_handler.setLevel(level)
        fe_handler.setFormatter(JsonLineFormatter())
        frontend_logger.addHandler(fe_handler)
        fe_console = logging.StreamHandler(sys.stdout)
        fe_console.setLevel(getattr(logging, settings.LOG_CONSOLE_LEVEL.upper(), logging.INFO))
        fe_console.setFormatter(HumanFormatter(use_color=sys.stdout.isatty()))
        frontend_logger.addHandler(fe_console)
        frontend_logger.setLevel(level)
    except Exception as e:
        print(f"[logging] cannot open frontend.log handler: {e}", file=sys.stderr)
        frontend_logger.addHandler(logging.NullHandler())

    # 4) Uvicorn: reuse our root sink for its messages; our middleware does
    # access logging, so silence uvicorn's own access log (no duplicates).
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        u = logging.getLogger(name)
        u.handlers.clear()
        if name == "uvicorn.access":
            u.setLevel(logging.CRITICAL)
            u.propagate = False
        else:
            u.setLevel(level)
            u.propagate = True

    # 5) Noisy third parties.
    logging.getLogger("pyrogram").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    logging.getLogger("tmusic.logging").info(
        "Logging initialized",
        extra={
            "log_dir": str(log_dir),
            "level": logging.getLevelName(level),
            "log_format": "jsonl",
            "rotation_max_bytes": settings.LOG_MAX_BYTES,
            "archive_keep": settings.LOG_ARCHIVE_KEEP,
            "archive_encrypted": pyzipper is not None,
        },
    )


def get_frontend_logger() -> logging.Logger:
    return logging.getLogger(_FRONTEND_LOGGER_NAME)
