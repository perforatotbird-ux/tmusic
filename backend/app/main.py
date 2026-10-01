"""
T-Music Main Application
Industrial-grade, internet-facing music catalog and streaming server.

Hardening (2026-09 security audit):
- serve_spa: path traversal (C-1) fixed via resolve() + is_relative_to().
- CSP: 'unsafe-inline' removed from script-src; deprecated X-XSS-Protection
  header removed; HSTS emitted when COOKIE_SECURE=true (i.e. behind HTTPS).
- /api/* sliding-window rate limit (120 req/min per IP), stricter 10 req/min
  on the auth endpoints, on top of the existing login lockout.
- Startup self-heals the admin account from ADMIN_PASSWORD_HASH and warns
  loudly when REQUIRE_AUTH is disabled.
"""
import logging
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import settings
from app.core.logging_config import (
    setup_logging, set_request_id, reset_request_id,
)

# Logging MUST be configured before any request is served (and before the
# lifespan logger emits): JSONL file + human console + encrypted rotation.
setup_logging()

from app.core.csrf import CSRF_COOKIE, csrf_check_failed
from app.core.database import (
    wait_for_database,
    ensure_performance_indexes,
    ensure_auth_schema,
    ensure_trigram_indexes,
    ensure_feature_tables,
    ensure_catalog_schema,
    SessionLocal,
)
from app.core.token_store import purge_expired_revocations
from app.core.rate_limiter import limiter
from app.tg.manager import tg_manager
from app.services.cover_cache_service import cover_cache
from app.services.bootstrap import ensure_admin_bootstrap
from app.utils.client_ip import get_client_ip

from app.api.auth_router import router as auth_router
from app.api.library_router import router as library_router
from app.api.stream_router import router as stream_router
from app.api.system_router import router as system_router
from app.api.playback_router import router as playback_router
from app.api.playlist_router import router as playlist_router
from app.api.share_router import router as share_router
from app.api.metadata_router import router as metadata_router
from app.api.subsonic_router import router as subsonic_router
from app.api.subsonic_admin_router import router as subsonic_admin_router
from app.api.vfs_router import router as vfs_router
from app.api.admin_router import router as admin_router
from app.api.admin_catalog_router import router as admin_catalog_router
from app.api.admin_vfs_router import router as admin_vfs_router
from app.services.backup_scheduler import start_backup_scheduler, stop_backup_scheduler
from app.services.discovery_service import discovery_service
from app.services.transcoding_service import transcoding_service
from app.api.logs_router import router as logs_router
from app.services import admin_service

logger = logging.getLogger("tmusic.main")

# ─── Rate-limit policy (requests per minute per client IP) ────────────────────
# Budgets live in music.env (API_RATE_LIMIT_PER_MIN / AUTH_RATE_LIMIT_PER_MIN).
API_RATE_LIMIT_PER_MIN = settings.API_RATE_LIMIT_PER_MIN
AUTH_RATE_LIMIT_PER_MIN = settings.AUTH_RATE_LIMIT_PER_MIN
AUTH_SENSITIVE_PATHS = frozenset({"/api/auth/login"})
_REDACTED_QUERY_KEYS = frozenset({"p", "t", "s", "token", "password", "authorization", "access_token", "csrf_token"})


def _safe_log_path(path: str) -> str:
    if path.startswith("/s/") and len(path) > 3:
        return "/s/[redacted]"
    return path


def _safe_log_query(query: str) -> str | None:
    if not query:
        return None
    pairs = parse_qsl(query, keep_blank_values=True)
    return urlencode([(key, "[redacted]" if key.lower() in _REDACTED_QUERY_KEYS else value) for key, value in pairs])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan managing database connectivity and MTProto client lifecycle."""
    logger.info("Starting T-Music Server (%s, Environment: %s)...", settings.PROJECT_NAME, settings.ENVIRONMENT)

    if not settings.REQUIRE_AUTH:
        logger.warning(
            "SECURITY WARNING: REQUIRE_AUTH=false — the library API is exposed "
            "without authentication. Do not use this on an Internet-facing host."
        )

    # 1. Database readiness check
    db_ok = wait_for_database(timeout=15)
    if not db_ok:
        logger.error("Database connection failed on startup. Exiting.")
    else:
        logger.info("Database connection established successfully.")
        ensure_performance_indexes()
        ensure_auth_schema()
        ensure_trigram_indexes()
        ensure_feature_tables()
        ensure_catalog_schema()
        try:
            with SessionLocal() as db:
                removed = purge_expired_revocations(db)
                if removed:
                    logger.info("Purged %d expired token revocations.", removed)
        except Exception as e:
            logger.warning("Revocation purge failed: %s", e)
        # Self-heal admin credentials (removes default/legacy passwords)
        try:
            with SessionLocal() as db:
                ensure_admin_bootstrap(db)
        except Exception as e:
            logger.error("Admin bootstrap failed: %s", e)

    # 2. Telegram MTProto multi-account cluster initialization
    await tg_manager.start()

    # 3. Scheduled DB backups (admin panel config)
    try:
        start_backup_scheduler()
    except Exception as e:
        logger.warning("Backup scheduler failed to start: %s", e)

    # 4. LAN Discovery Service (UDP broadcast responder for mobile clients)
    try:
        await discovery_service.start(http_port=settings.PORT, project_name=settings.PROJECT_NAME)
    except Exception as e:
        logger.warning("Discovery service failed to start: %s", e)

    # 5. Transcoding Service (background ffmpeg workers for non-browser audio formats)
    try:
        transcoding_service.start()
    except Exception as e:
        logger.warning("Transcoding service failed to start: %s", e)

    yield

    # 5. Graceful shutdown
    logger.info("Shutting down T-Music Server...")
    try:
        await stop_backup_scheduler()
    except Exception:
        pass
    await discovery_service.stop()
    await transcoding_service.shutdown()
    await tg_manager.stop()
    await cover_cache.close()
    logger.info("T-Music Server stopped cleanly.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Standalone, high-security Telegram Cloud Music Streaming Server",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Last-resort handler: full traceback to ERROR log, generic 500 to client."""
    rid = request.headers.get("X-Request-ID") or "-"
    logger.exception(
        "unhandled_exception",
        extra={
            "method": request.method,
            "path": request.url.path,
            "request_id": rid,
            "client_ip": request.client.host if request.client else None,
            "exc_type": type(exc).__name__,
        },
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})

# ─── CSRF Double-Submit Enforcement ──────────────────────────────────────────
@app.middleware("http")
async def csrf_protect_middleware(request: Request, call_next):
    """Reject cookie-authenticated mutations without a matching X-CSRF-Token.

    Bearer-authenticated requests, safe methods, and the session-less
    login/logout endpoints are exempt (see app.core.csrf).

    Fail-closed (2026-09 audit fix): a cookie-authenticated mutation with no
    csrf_token cookie at all is rejected too — the client re-arms via
    GET /api/auth/csrf and retries. The 403 body carries a stable `code` so
    the frontend can distinguish "re-arm and retry" from a real forgery.
    """
    if csrf_check_failed(request):
        ACCESS_LOG.warning(
            "csrf_rejected",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": 403,
                "client_ip": request.client.host if request.client else None,
            },
        )
        missing_cookie = not request.cookies.get(CSRF_COOKIE)
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "CSRF cookie missing — re-arm via GET /api/auth/csrf and retry."
                    if missing_cookie
                    else "CSRF validation failed."
                ),
                "code": "csrf_cookie_missing" if missing_cookie else "csrf_invalid",
            },
        )
    return await call_next(request)


# ─── API Rate-Limiting Middleware ─────────────────────────────────────────────
@app.middleware("http")
async def api_rate_limit_middleware(request: Request, call_next):
    """Sliding-window per-IP rate limit for the API surface (static/SPA exempt)."""
    path = request.url.path
    if path.startswith("/api/"):
        client_ip = get_client_ip(request)
        if path in AUTH_SENSITIVE_PATHS:
            limited = limiter.is_rate_limited(f"auth:{client_ip}", AUTH_RATE_LIMIT_PER_MIN, 60)
        else:
            limited = limiter.is_rate_limited(f"api:{client_ip}", API_RATE_LIMIT_PER_MIN, 60)
        if limited:
            ACCESS_LOG.warning(
                "rate_limited",
                extra={
                    "method": request.method,
                    "path": path,
                    "status": 429,
                    "client_ip": client_ip,
                    "budget_per_min": AUTH_RATE_LIMIT_PER_MIN if path in AUTH_SENSITIVE_PATHS else API_RATE_LIMIT_PER_MIN,
                },
            )
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Slow down."},
                headers={"Retry-After": "60"},
            )
    elif path.startswith("/rest/"):
        # Subsonic clients poll/scrape libraries; allow a much higher budget
        # than the web SPA API while still capping runaway loops.
        client_ip = get_client_ip(request)
        limited = limiter.is_rate_limited(f"subsonic:{client_ip}", 600, 60)
        if limited:
            return JSONResponse(
                content={
                    "subsonic-response": {
                        "status": "failed",
                        "version": "1.16.1",
                        "error": {"code": 0, "message": "Too many requests."},
                    }
                },
                status_code=429,
                headers={"Retry-After": "60"},
            )
    return await call_next(request)

# ─── High-Security Headers Middleware ──────────────────────────────────────────
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: https: blob:; "
        "media-src 'self' blob:; "
        "script-src 'self' 'sha256-cEEJXZkc6tLNJmk1I/w8YnMuXFWQuspQWrTyhDyTmmY='; "
        "script-src-elem 'self' 'sha256-cEEJXZkc6tLNJmk1I/w8YnMuXFWQuspQWrTyhDyTmmY='; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' https: ws:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    if settings.COOKIE_SECURE:
        # Only meaningful over HTTPS; COOKIE_SECURE=true implies an HTTPS deployment
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# ─── Access Log + Request-ID Middleware (outermost: added last) ───────────────
ACCESS_LOG = logging.getLogger("tmusic.access")

@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    """Structured HTTP access log: method, path, status, duration, request_id.

    Runs first (outermost) so it also observes CSRF/rate-limit short-circuits.
    Static asset noise (no /api|/rest|/health prefix, GET) is logged at DEBUG.
    """
    if not settings.LOG_ACCESS_ENABLED:
        return await call_next(request)

    rid = uuid.uuid4().hex[:12]
    token = set_request_id(rid)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            ACCESS_LOG.exception(
                "request_failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": 500,
                    "duration_ms": duration_ms,
                    "client_ip": request.client.host if request.client else None,
                },
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        path = _safe_log_path(request.url.path)
        quiet = (
            status < 400
            and request.method == "GET"
            and not (path.startswith(("/api/", "/rest/", "/health")))
        )
        level = logging.DEBUG if quiet else (logging.WARNING if status >= 400 else logging.INFO)
        ACCESS_LOG.log(
            level,
            "%s %s -> %s (%.2fms)",
            request.method, path, status, duration_ms,
            extra={
                "method": request.method,
                "path": path,
                "status": status,
                "duration_ms": duration_ms,
                "client_ip": request.client.host if request.client else None,
                "query": _safe_log_query(str(request.url.query)),
            },
        )
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        reset_request_id(token)


# ─── CORS Middleware ─────────────────────────────────────────────────────────
# 2026-09-29: the private-range regex is gated by CORS_ALLOW_LAN — turn it
# off once every client is covered by an explicit ALLOWED_ORIGINS entry.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_origin_regex=(
        r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?$"
        if settings.CORS_ALLOW_LAN
        else None
    ),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition"]
)


# ─── Trusted Host Middleware (2026-09-29) ────────────────────────────────────
# Rejects requests with an unexpected Host header (400) before routing —
# Host-header poisoning otherwise flows into generated share URLs
# (share_router._share_url uses request.base_url). Default ["*"] preserves
# the previous behavior; lock down via TRUSTED_HOSTS in music.env.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)


# ─── Maintenance-flag short TTL cache (minor 6) ─────────────────────────────
# maintenance_middleware previously ran a DB query for EVERY /api/ request.
# The flag changes at human speed, so a 5-second cache keeps the check O(0)
# DB queries in the hot path while still switching over promptly.
_MAINTENANCE_CACHE_TTL = 5.0
_maintenance_cache: dict = {"value": False, "checked_at": 0.0}


def _maintenance_enabled() -> bool:
    import time as _time

    now = _time.monotonic()
    if now - _maintenance_cache["checked_at"] < _MAINTENANCE_CACHE_TTL:
        return _maintenance_cache["value"]
    try:
        with SessionLocal() as db:
            enabled = str(admin_service.get_setting(db, "maintenance_mode", "false")).lower() in ("1", "true", "yes", "on")
    except Exception:
        enabled = False
    _maintenance_cache["value"] = enabled
    _maintenance_cache["checked_at"] = now
    return enabled


def reset_maintenance_cache() -> None:
    """Drop the TTL cache (used by tests that monkeypatch get_setting)."""
    _maintenance_cache["value"] = False
    _maintenance_cache["checked_at"] = 0.0


@app.middleware("http")
async def maintenance_middleware(request: Request, call_next):
    path = request.url.path
    allowed = (
        request.method == "OPTIONS"
        or path == "/health"
        or path.startswith("/api/auth/")
        or path.startswith("/api/admin/")
        or path == "/api/system/config"
    )
    if not allowed:
        if _maintenance_enabled() and (path.startswith("/api/") or path.startswith("/rest/")):
            return JSONResponse(
                status_code=503,
                content={"detail": "Service is temporarily unavailable for maintenance."},
                headers={"Retry-After": "300", "X-Maintenance-Mode": "1"},
            )
    return await call_next(request)


# ─── Actual-byte request body caps, before JSON/multipart parsing ──────────
# Uploads have explicit larger limits; ordinary API bodies stay capped at 1 MB.
from app.core.body_limits import BodySizeLimitMiddleware


app.add_middleware(BodySizeLimitMiddleware)


# ─── Include API Routers ──────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(library_router)
app.include_router(stream_router)
app.include_router(system_router)
app.include_router(playback_router)
app.include_router(playlist_router)
app.include_router(share_router)
app.include_router(metadata_router)
app.include_router(subsonic_router)
app.include_router(subsonic_admin_router)
app.include_router(vfs_router)
app.include_router(admin_router)
app.include_router(admin_catalog_router)
app.include_router(admin_vfs_router)
app.include_router(logs_router)


@app.get("/health", tags=["system"])
async def health():
    """Liveness probe: no auth, no secrets."""
    return {
        "status": "ok",
        "project": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
    }

# ─── Frontend Static Files & SPA Routing ──────────────────────────────────────
FRONTEND_DIST = (settings.BASE_DIR / "frontend" / "dist").resolve()

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """
        SPA file serving with path-traversal defense.

        The catch-all route receives percent-decoded input, so "..%2F" arrives
        here as "../". We therefore resolve the candidate against FRONTEND_DIST
        and refuse anything that escapes the document root.
        """
        try:
            candidate = (FRONTEND_DIST / full_path).resolve()
        except (OSError, ValueError):
            return FileResponse(FRONTEND_DIST / "index.html")

        if candidate.is_relative_to(FRONTEND_DIST) and candidate.is_file():
            return FileResponse(candidate)
        # Unknown in-root paths fall through to the SPA shell; escape attempts
        # are indistinguishable from client-side routes by design.
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    async def index():
        return {
            "name": settings.PROJECT_NAME,
            "status": "online",
            "message": "Backend running. Frontend dev server is available on port 5175 or build dist/."
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
