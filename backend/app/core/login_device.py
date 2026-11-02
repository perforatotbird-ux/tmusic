"""Known-device cookie for brute-force lockout without account-wide DoS.

2026-11-02 audit (H-1): five wrong passwords from ANY address used to lock the
account for everyone, so a single anonymous client could keep the owner locked
out forever. Following the OWASP "device cookie" pattern, a browser that has
already logged in successfully receives a signed, HttpOnly cookie. While it is
valid:

* the account-wide lockout (and the per-IP lockout) does not apply to it;
* its own failures are counted per device and lock only that device.

Untrusted clients keep the existing per-IP and per-account limits. The cookie
is bound to the user id and token_version, so a password change or "revoke all
sessions" invalidates every remembered device.
"""
import base64
import hashlib
import hmac
import secrets
import time
from typing import Optional

from app.core.config import settings

DEVICE_COOKIE = "login_device"
DEVICE_COOKIE_PATH = "/api/auth"
_VERSION = "v1"


def _key() -> bytes:
    return hashlib.sha256(b"tmusic-login-device:" + settings.SESSION_SECRET.encode("utf-8")).digest()


def _sign(payload: str) -> str:
    mac = hmac.new(_key(), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def issue_device_token(user_id: int, token_version: int) -> str:
    expires = int(time.time()) + settings.LOGIN_DEVICE_TRUST_DAYS * 86400
    nonce = secrets.token_urlsafe(12)
    payload = f"{_VERSION}.{int(user_id)}.{int(token_version)}.{expires}.{nonce}"
    return f"{payload}.{_sign(payload)}"


def device_id_for(token: Optional[str], user_id: int, token_version: int) -> Optional[str]:
    """Return a stable device id when the cookie is valid for this account."""
    if not token or not isinstance(token, str) or len(token) > 256:
        return None
    parts = token.split(".")
    if len(parts) != 6 or parts[0] != _VERSION:
        return None
    payload, sig = ".".join(parts[:5]), parts[5]
    if not hmac.compare_digest(_sign(payload), sig):
        return None
    try:
        uid, tv, expires = int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return None
    if uid != user_id or tv != (token_version or 0) or expires < time.time():
        return None
    return parts[4]


def device_cookie_kwargs(secure: bool) -> dict:
    return {
        "key": DEVICE_COOKIE,
        "httponly": True,
        "samesite": "strict",
        "secure": secure,
        "path": DEVICE_COOKIE_PATH,
        "max_age": settings.LOGIN_DEVICE_TRUST_DAYS * 86400,
    }
