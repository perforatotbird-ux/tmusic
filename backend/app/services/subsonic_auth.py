"""
T-Music Subsonic auth helpers.

2026-09 audit hardening (M-1): the PANEL bcrypt password is no longer
accepted in the query string (`p=`). URL-logging the main account password
was the actual risk; the dedicated Subsonic token carries no privileges
outside /rest and rotates without touching the panel credential.

Supported modes:
  - p=<subsonic token>      (token as password)
  - t=md5(token+salt)&s=salt (token auth)
The token is stored Fernet-encrypted on app_users.subsonic_token_enc.
"""
import base64
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from typing import Optional

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.rate_limiter import limiter
from app.core.security import hash_password, verify_password
from app.core.user_cache import get_attached_user, invalidate_user
from app.models.auth import AppUser
from app.utils.time import utcnow

logger = logging.getLogger("tmusic.subsonic")
_DUMMY_HASH = hash_password("tmusic-subsonic-dummy")


def _fernet() -> Optional[Fernet]:
    if not settings.SESSION_SECRET:
        return None
    key = hashlib.sha256(settings.SESSION_SECRET.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_token(token: str) -> Optional[str]:
    f = _fernet()
    if not f:
        return None
    return f.encrypt(token.encode()).decode()


def decrypt_token(enc: Optional[str]) -> Optional[str]:
    if not enc:
        return None
    f = _fernet()
    if not f:
        return None
    try:
        return f.decrypt(enc.encode()).decode()
    except Exception:
        logger.debug("Failed to decrypt Subsonic token", exc_info=True)
        return None


def generate_subsonic_token() -> str:
    return secrets.token_urlsafe(24)


def ensure_subsonic_token(db: Session, user: AppUser, *, rotate: bool = False) -> str:
    """Return existing decrypted token or mint+persist a new one."""
    user = get_attached_user(db, user)
    if user is None:
        raise ValueError("User not found.")
    existing = None if rotate else decrypt_token(user.subsonic_token_enc)
    if existing:
        return existing
    token = generate_subsonic_token()
    enc = encrypt_token(token)
    if not enc:
        # Never report a successful rotation unless the new secret can persist.
        raise RuntimeError("Subsonic credentials are unavailable.")
    user.subsonic_token_enc = enc
    db.commit()
    invalidate_user(user.username)
    return token


def _record_failure(client_ip: Optional[str] = None, username: Optional[str] = None, *args, **kwargs) -> None:
    # Retain compatibility if legacy (db, user) positional args are passed
    target_ip = client_ip if isinstance(client_ip, str) else kwargs.get("client_ip")
    if target_ip:
        limiter.record_failure(
            f"subsonic_{target_ip}",
            max_attempts=settings.MAX_FAILED_LOGIN_ATTEMPTS,
            lockout_seconds=settings.LOCKOUT_MINUTES * 60,
        )
    logger.warning("Subsonic auth failure: ip=%s user=%r", target_ip, username)


def _record_success(client_ip: Optional[str] = None, username: Optional[str] = None, *args, **kwargs) -> None:
    target_ip = client_ip if isinstance(client_ip, str) else kwargs.get("client_ip")
    if target_ip:
        limiter.record_success(f"subsonic_{target_ip}")


def authenticate_subsonic(
    db: Session,
    username: Optional[str],
    password: Optional[str],
    token: Optional[str],
    salt: Optional[str],
    client_ip: Optional[str] = None,
) -> Optional[AppUser]:
    """Authenticate a Subsonic client against the DEDICATED token only.

    The panel bcrypt password is deliberately NOT accepted here anymore:
    it used to travel in the query string (`p=<panel password>`), which leaks
    the main account credential into proxy logs and browser history.

    Failed attempts are tracked by client IP via rate_limiter and do NOT
    lock the web user account (mitigating SEC-002 unauthenticated DoS).
    """
    if client_ip:
        locked, _ = limiter.is_locked_out(f"subsonic_{client_ip}")
        if locked:
            logger.warning("Subsonic request from locked-out IP: %s", client_ip)
            return None

    if not username:
        return None
    user = db.scalar(select(AppUser).where(AppUser.username == username))
    if not user or not user.is_active:
        verify_password(password or "x", _DUMMY_HASH)
        _record_failure(client_ip=client_ip, username=username)
        return None
    if user.locked_until and user.locked_until > utcnow():
        verify_password(password or "x", _DUMMY_HASH)
        _record_failure(client_ip=client_ip, username=username)
        return None

    authenticated = False
    shared = decrypt_token(user.subsonic_token_enc)

    if password and shared:
        # p=<token> — compare against the dedicated token only.
        authenticated = hmac.compare_digest(password, shared)

    if not authenticated and token and salt and shared:
        expected = hashlib.md5((shared + salt).encode("utf-8")).hexdigest()
        authenticated = hmac.compare_digest(token.lower(), expected.lower())

    if authenticated:
        _record_success(client_ip=client_ip, username=username)
        return user
    _record_failure(client_ip=client_ip, username=username)
    return None
