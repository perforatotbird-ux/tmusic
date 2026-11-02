"""
T-Music Request Utilities
Shared client-IP / scheme extraction used by auth endpoints, rate limiting,
cookies and logs.

Proxy headers are honored ONLY when TRUST_PROXY_HEADERS=true, and then only for
peers listed in TRUSTED_PROXY_IPS (when that list is set).

2026-11-02 audit (M-2): the client IP used to be the LEFT-most X-Forwarded-For
entry. A reverse proxy *appends* the peer address on the right, so everything on
the left is whatever the client sent — any client could pick its own IP and
walk around per-IP rate limits and lockouts. The client IP is now the entry
added by the outermost trusted proxy: the TRUSTED_PROXY_HOPS-th from the right.
"""
import ipaddress
from functools import lru_cache
from typing import Optional, Tuple

from starlette.requests import HTTPConnection

from app.core.config import settings


@lru_cache(maxsize=1)
def _trusted_networks(raw: Tuple[str, ...]) -> tuple:
    nets = []
    for item in raw:
        try:
            nets.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            continue
    return tuple(nets)


def _peer(request: HTTPConnection) -> str:
    return request.client.host if request.client else "127.0.0.1"


def proxy_headers_trusted(request: HTTPConnection) -> bool:
    if not settings.TRUST_PROXY_HEADERS:
        return False
    allow = tuple(settings.TRUSTED_PROXY_IPS or ())
    if not allow:
        return True
    try:
        peer = ipaddress.ip_address(_peer(request))
    except ValueError:
        return False
    return any(peer in net for net in _trusted_networks(allow))


def _valid_ip(value: str) -> Optional[str]:
    value = (value or "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def get_client_ip(request: HTTPConnection) -> str:
    """Client IP for rate limiting and audit logs."""
    if proxy_headers_trusted(request):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",") if p.strip()]
            hops = settings.TRUSTED_PROXY_HOPS
            if parts:
                # Fewer entries than proxies: take the left-most available,
                # which was still written by a trusted proxy.
                candidate = parts[-hops] if len(parts) >= hops else parts[0]
                ip = _valid_ip(candidate)
                if ip:
                    return ip
        real_ip = _valid_ip(request.headers.get("X-Real-IP", ""))
        if real_ip:
            return real_ip
    return _peer(request)


def is_https_request(request: HTTPConnection) -> bool:
    if request.url.scheme in ("https", "wss"):
        return True
    if proxy_headers_trusted(request):
        proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[-1].strip().lower()
        return proto == "https"
    return False


def cookie_secure(request: Optional[HTTPConnection]) -> bool:
    """Effective Secure flag: explicit COOKIE_SECURE wins, otherwise follow HTTPS."""
    if settings.COOKIE_SECURE is not None:
        return bool(settings.COOKIE_SECURE)
    return bool(request is not None and is_https_request(request))
