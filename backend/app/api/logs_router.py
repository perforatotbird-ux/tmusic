"""
T-Music Client Logs API
Ingests structured events emitted by the browser frontend and appends them
to backend/storage/log/frontend.log (JSONL, same rotation/encryption as the
backend stream).

Abuse resistance:
- POST-only, payload capped (≤50 events, msg ≤2000 chars, logger ≤64 chars).
- Level whitelisted to the five standard names.
- Shared /api/* sliding-window rate limit already applies.
- Bearer/cookie auth NOT required (login-page errors must be loggable);
  CSRF applies automatically for cookie sessions (mutations via /api/*).
"""
import logging
import re
from typing import List, Literal, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.core.logging_config import get_frontend_logger, utc_iso
from app.utils.client_ip import get_client_ip

router = APIRouter(prefix="/api/system", tags=["logs"])
log = get_frontend_logger()

MAX_EVENTS_PER_BATCH = 50
MAX_MSG_LEN = 2000
MAX_LOGGER_LEN = 64

# Defense-in-depth: the client already redacts, but never trust client payloads.
_SENSITIVE_KEY = re.compile(
    r"(pass|pwd|secret|token|authorization|cookie|session_string|api_hash|api_key|password_hash|csrf)",
    re.IGNORECASE,
)
_REDACTED = "[redacted]"


def _sanitize(obj, depth: int = 0):
    if depth > 4:
        return "[depth]"
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if _SENSITIVE_KEY.search(str(k)) else _sanitize(v, depth + 1))
            for k, v in list(obj.items())[:50]
        }
    if isinstance(obj, list):
        return [_sanitize(v, depth + 1) for v in obj[:20]]
    if isinstance(obj, str) and len(obj) > MAX_MSG_LEN:
        return obj[:MAX_MSG_LEN] + "…"
    return obj

Level = Literal["DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "FATAL"]

_LEVEL_ALIAS = {"WARN": "WARNING", "FATAL": "CRITICAL"}


class ClientEvent(BaseModel):
    ts: Optional[str] = None                 # client ISO timestamp; server adds its own
    level: Level = "INFO"
    logger: str = Field(default="frontend", max_length=MAX_LOGGER_LEN)
    msg: str = Field(default="", max_length=MAX_MSG_LEN)
    # Arbitrary structured context (component, action, error, url, ...).
    data: Optional[dict] = None


class ClientLogBatch(BaseModel):
    source: Literal["web", "android"] = "web"
    session_id: Optional[str] = Field(default=None, max_length=64)
    events: List[ClientEvent] = Field(min_length=1, max_length=MAX_EVENTS_PER_BATCH)


@router.post("/client-logs")
async def ingest_client_logs(batch: ClientLogBatch, request: Request):
    """Append a batch of frontend events to frontend.log."""
    ip = get_client_ip(request)
    ua = (request.headers.get("user-agent") or "")[:200]
    written = 0
    for ev in batch.events:
        extra = {
            "source": batch.source,
            "client_ts": ev.ts,
            "session_id": batch.session_id,
            "client_ip": ip,
            "user_agent": ua or None,
            "event_logger": ev.logger,
        }
        if ev.data:
            extra["data"] = _sanitize(ev.data)
        # Server-side ts is added by the formatter; client_ts preserved above.
        level_name = _LEVEL_ALIAS.get(ev.level, ev.level)
        log.log(
            getattr(logging, level_name, logging.INFO),
            ev.msg or "(empty)",
            extra=extra,
        )
        written += 1
    return {"status": "ok", "accepted": written, "server_ts": utc_iso()}
