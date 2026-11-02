"""Bound actual request bytes before body parsing, including multipart uploads.

2026-11-02 audit (M-1): the 257 MB import endpoints used to spool the whole
body to disk *before* authentication, so any anonymous client could make the
server write hundreds of megabytes per request. Now:

* paths whose limit exceeds ``PREAUTH_THRESHOLD_BYTES`` are authorized from
  the request headers (Bearer header or session cookie) before a single body
  byte is read; anonymous / unprivileged requests get 401/403 immediately;
* at most ``MAX_CONCURRENT_LARGE_UPLOADS`` such bodies are spooled at once
  (excess requests get 429 instead of filling the disk in parallel);
* the middleware is registered innermost in app.main, so TrustedHost, CORS,
  rate limiting and CSRF run before it.
"""
from tempfile import SpooledTemporaryFile
from typing import Optional, Tuple
from urllib.parse import unquote

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

MAX_JSON_BODY_BYTES = 1024 * 1024
# Includes a bounded allowance for multipart framing. Exact paths only: import
# tree-run/preview JSON requests retain the small default limit.
UPLOAD_BODY_LIMITS = {
    "/api/auth/avatar": 11 * 1024 * 1024,
    "/api/library/cover": 11 * 1024 * 1024,
    "/api/admin/vfs/import/preview": 257 * 1024 * 1024,
    "/api/admin/vfs/import/upload": 257 * 1024 * 1024,
}
# Bodies larger than this are only accepted from an already authorized user.
PREAUTH_THRESHOLD_BYTES = 32 * 1024 * 1024
# Permission required (in addition to a valid session) per large-upload path.
LARGE_UPLOAD_PERMISSIONS = {
    "/api/admin/vfs/import/preview": "admin.backups.manage",
    "/api/admin/vfs/import/upload": "admin.backups.manage",
}
MAX_CONCURRENT_LARGE_UPLOADS = 2
_large_uploads_in_flight = 0


def _authorize_sync(scope, permission: Optional[str]) -> Optional[Tuple[int, str]]:
    """Header/cookie-only auth check. Returns (status, detail) on denial."""
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    from starlette.requests import Request

    from app.api.dependencies import _authenticate_request, user_has_permission
    from app.core.database import SessionLocal

    request = Request(scope)
    credentials = None
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=value.strip())
    try:
        with SessionLocal() as db:
            user = _authenticate_request(request, credentials, db)
            if permission and not user_has_permission(user, permission):
                return 403, "Permission required."
    except HTTPException as exc:
        return exc.status_code, str(exc.detail)
    return None


async def preauthorize_large_upload(scope, path: str) -> Optional[Tuple[int, str]]:
    return await run_in_threadpool(_authorize_sync, scope, LARGE_UPLOAD_PERMISSIONS.get(path))


class BodySizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = unquote(scope.get("path", ""))
        if (scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH")
                or not path.startswith(("/api/", "/rest/"))):
            return await self.app(scope, receive, send)
        normalized = path.rstrip("/")
        limit = UPLOAD_BODY_LIMITS.get(normalized, MAX_JSON_BODY_BYTES)
        for key, value in scope.get("headers", ()):
            if key.lower() == b"content-length":
                try:
                    if int(value) > limit:
                        return await self._reject(scope, receive, send)
                except ValueError:
                    pass
        if limit <= PREAUTH_THRESHOLD_BYTES:
            return await self._spool(scope, receive, send, limit)

        denied = await preauthorize_large_upload(scope, normalized)
        if denied:
            status, detail = denied
            return await JSONResponse(status_code=status, content={"detail": detail})(scope, receive, send)
        global _large_uploads_in_flight
        if _large_uploads_in_flight >= MAX_CONCURRENT_LARGE_UPLOADS:
            return await JSONResponse(
                status_code=429,
                content={"detail": "Too many concurrent uploads. Try again later."},
                headers={"Retry-After": "30"},
            )(scope, receive, send)
        _large_uploads_in_flight += 1
        try:
            return await self._spool(scope, receive, send, limit)
        finally:
            _large_uploads_in_flight -= 1

    async def _spool(self, scope, receive, send, limit):
        # Spool large, permitted uploads to disk rather than keeping 256 MB in
        # RAM. Nothing reaches multipart parsing until the full size is verified.
        with SpooledTemporaryFile(max_size=MAX_JSON_BODY_BYTES) as body:
            size = 0
            while True:
                message = await receive()
                if message.get("type") != "http.request":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limit:
                    return await self._reject(scope, receive, send)
                await run_in_threadpool(body.write, chunk)
                if not message.get("more_body", False):
                    break
            await run_in_threadpool(body.seek, 0)
            remaining = size
            finished = False

            async def replay():
                nonlocal remaining, finished
                if finished:
                    return await receive()
                chunk = await run_in_threadpool(body.read, min(remaining, 64 * 1024))
                remaining -= len(chunk)
                finished = remaining == 0
                return {"type": "http.request", "body": chunk, "more_body": not finished}

            return await self.app(scope, replay, send)

    @staticmethod
    async def _reject(scope, receive, send):
        response = JSONResponse(status_code=413, content={"detail": "Request body too large."})
        return await response(scope, receive, send)
