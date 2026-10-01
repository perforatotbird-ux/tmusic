"""
T-Music CSRF Defense (double-submit cookie pattern).

The SPA authenticates with an HttpOnly session cookie, so state-changing
endpoints are reachable by cross-site form/fetch navigation. SameSite=Lax
blocks most of it, but top-level GET-navigations and lax-bypass quirks remain.
Every mutating /api/* request authenticated via cookie must therefore echo
the readable `csrf_token` cookie in the `X-CSRF-Token` header.

Fail-closed policy (2026-09 audit fix, was previously "let through"):
a mutating request carrying an `access_token` cookie but NO `csrf_token`
cookie is REJECTED with 403 (code `csrf_cookie_missing`) instead of being
silently accepted. The client then recovers automatically: GET /api/auth/csrf
sets a fresh csrf_token cookie (it is cheap, unauthenticated, and leaks
nothing), after which the retried mutation validates normally. This removes
the silent-degradation window where sessions predating the defense shipped —
or whose csrf cookie was wiped client-side — mutated without any CSRF check.

Requests carrying an `Authorization: Bearer` header are NOT cookie-authenticated
and skip the check. Login/logout are exempt (no session yet / self-invalidating).
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
    "/api/auth/logout",
    # Cookie-recovery endpoint: it must be reachable exactly when the CSRF
    # cookie is missing, otherwise the fail-closed rule would deadlock the
    # client (403 on every mutation, no way to obtain a cookie).
    "/api/auth/csrf",
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


def has_cookie_session(request: Request) -> bool:
    """True when the request authenticates via the session cookie."""
    return "access_token" in request.cookies


def has_bearer_credentials(request: Request) -> bool:
    """True only for a well-formed `Authorization: Bearer <token>` header.

    2026-09-29: previously ANY Authorization header (even garbage that
    HTTPBearer cannot parse) skipped the CSRF check while auth silently fell
    back to the victim's cookie — a CSRF bypass for cross-origin requests
    able to set headers (preflight-passing origins). Now only a real Bearer
    credential skips the check; with a Bearer credential the token (not the
    cookie) decides auth, so there is no cross-site confused-deputy left.
    """
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return scheme.lower() == "bearer" and bool(token.strip())


def csrf_check_failed(request: Request) -> bool:
    """True when a mutating API request needs CSRF validation and it fails.

    Fail-closed: a cookie-authenticated mutation without a csrf_token cookie
    fails (the caller answers 403 and the client calls GET /api/auth/csrf to
    re-arm, then retries).
    """
    if request.method in SAFE_METHODS:
        return False
    path = request.url.path
    if not path.startswith("/api/"):
        return False
    if path in CSRF_EXEMPT_PATHS:
        return False
    # Bearer-authenticated requests are not cookie-authenticated: no CSRF surface.
    if has_bearer_credentials(request):
        return False
    if not has_cookie_session(request):
        return False
    expected = request.cookies.get(CSRF_COOKIE)
    if not expected:
        # Fail CLOSED: without the readable cookie there is nothing to compare
        # against, so the request cannot prove it is same-origin. The client
        # re-arms via GET /api/auth/csrf and retries the mutation.
        return True
    return request.headers.get(CSRF_HEADER) != expected
