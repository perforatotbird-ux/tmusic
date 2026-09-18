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
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.core.config import settings
from app.core.csrf import csrf_check_failed
from app.core.database import (
    wait_for_database,
    ensure_performance_indexes,
    ensure_auth_schema,
    ensure_trigram_indexes,
    ensure_feature_tables,
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
from app.api.metadata_router import router as metadata_router

logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tmusic.main")

# ─── Rate-limit policy (requests per minute per client IP) ────────────────────
# Budgets live in music.env (API_RATE_LIMIT_PER_MIN / AUTH_RATE_LIMIT_PER_MIN).
API_RATE_LIMIT_PER_MIN = settings.API_RATE_LIMIT_PER_MIN
AUTH_RATE_LIMIT_PER_MIN = settings.AUTH_RATE_LIMIT_PER_MIN
AUTH_SENSITIVE_PATHS = frozenset({"/api/auth/login"})


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

    yield

    # 3. Graceful shutdown
    logger.info("Shutting down T-Music Server...")
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

# ─── CSRF Double-Submit Enforcement ──────────────────────────────────────────
@app.middleware("http")
async def csrf_protect_middleware(request: Request, call_next):
    """Reject cookie-authenticated mutations without a matching X-CSRF-Token.

    Bearer-authenticated requests, safe methods, and the session-less
    login/logout endpoints are exempt (see app.core.csrf).
    """
    if csrf_check_failed(request):
        return JSONResponse(
            status_code=403,
            content={"detail": "CSRF validation failed."},
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
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Slow down."},
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
        "script-src 'self'; "
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

# ─── CORS Middleware ─────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Disposition"]
)

# ─── Include API Routers ──────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(library_router)
app.include_router(stream_router)
app.include_router(system_router)
app.include_router(playback_router)
app.include_router(playlist_router)
app.include_router(metadata_router)


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
