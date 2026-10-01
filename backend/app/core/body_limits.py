"""Bound actual request bytes before body parsing, including multipart uploads."""
from tempfile import SpooledTemporaryFile
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


class BodySizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = unquote(scope.get("path", ""))
        if (scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH")
                or not path.startswith(("/api/", "/rest/"))):
            return await self.app(scope, receive, send)
        limit = UPLOAD_BODY_LIMITS.get(path.rstrip("/"), MAX_JSON_BODY_BYTES)
        for key, value in scope.get("headers", ()):
            if key.lower() == b"content-length":
                try:
                    if int(value) > limit:
                        return await self._reject(scope, receive, send)
                except ValueError:
                    pass
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
