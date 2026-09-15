"""
T-Music Request Utilities
Shared client-IP extraction used by auth endpoints and rate-limit middleware.
"""
from fastapi import Request

from app.core.config import settings


def get_client_ip(request: Request) -> str:
    """Client IP for rate limiting and audit logs.

    Proxy headers are honored ONLY when TRUST_PROXY_HEADERS=true
    (i.e. the app sits behind a reverse proxy that overwrites them).
    """
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
    return request.client.host if request.client else "127.0.0.1"
