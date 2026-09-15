"""
T-Music CSRF Defense (double-submit cookie pattern).

The SPA authenticates with an HttpOnly session cookie, so state-changing
endpoints are reachable by cross-site form/fetch navigation. SameSite=Lax
blocks most of it, but top-level GET-navigations and lax-bypass quirks remain.
Every mutating /api/* request authenticated via cookie must therefore echo
the readable `csrf_token` cookie in the `X-CSRF-Token` header.

Requests carrying an `Authorization: Bearer` header are NOT cookie-authenticated
and skip the check. Login/PIN/logout are exempt (no session yet / self-invalidating).
"""
import secrets

from fastapi import Request

from app.core.config import settings

CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"

# Session-less or self-invalidating endpoints — nothing to forge against.
# (Forced logout via CSRF is a nuisance at most; blocking it would brick
# logout for pre-CSRF sessions that have no csrf cookie yet.)
CSRF_EXEMPT_PATHS = frozenset({
    "/api/auth/login",
    "/api/auth/pin",
    "/api/auth/logout",
})

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_cookie_kwargs() -> dict:
    return {
        "key": CSRF_COOKIE,
        "httponly": False,  # JS must read it to echo into the header (double-submit)
        "samesite": "lax",
        "secure": settings.COOKIE_SECURE,
        "path": "/",
    }


def csrf_check_failed(request: Request) -> bool:
    """True when a mutating API request needs CSRF validation and it fails."""
    if request.method in SAFE_METHODS:
        return False
    path = request.url.path
    if not path.startswith("/api/"):
        return False
    if path in CSRF_EXEMPT_PATHS:
        return False
    # Bearer-authenticated requests are not cookie-authenticated: no CSRF surface.
    if request.headers.get("authorization"):
        return False
    if "access_token" not in request.cookies:
        return False
    expected = request.cookies.get(CSRF_COOKIE)
    if not expected:
        # Server never issued a CSRF cookie for this client (e.g. session from
        # before this defense shipped): let the request through rather than
        # bricking logout/cover flows; the next login issues the cookie.
        return False
    return request.headers.get(CSRF_HEADER) != expected
