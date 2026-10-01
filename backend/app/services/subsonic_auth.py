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
from app.core.security import hash_password, verify_password
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
        return None


def generate_subsonic_token() -> str:
    return secrets.token_urlsafe(24)


def ensure_subsonic_token(db: Session, user: AppUser) -> str:
    """Return existing decrypted token or mint+persist a new one."""
    existing = decrypt_token(user.subsonic_token_enc)
    if existing:
        return existing
    token = generate_subsonic_token()
    enc = encrypt_token(token)
    if not enc:
        # No SESSION_SECRET — still return token but cannot persist securely
        logger.warning("SESSION_SECRET missing; Subsonic token not persisted.")
        return token
    user.subsonic_token_enc = enc
    db.commit()
    return token


def _record_failure(db: Session, user: AppUser) -> None:
    user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
    if user.failed_login_attempts >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
        user.locked_until = utcnow() + timedelta(minutes=settings.LOCKOUT_MINUTES)
        logger.warning("Subsonic account locked: user=%r", user.username)
    db.commit()


def _record_success(db: Session, user: AppUser) -> None:
    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()


def authenticate_subsonic(
    db: Session,
    username: Optional[str],
    password: Optional[str],
    token: Optional[str],
    salt: Optional[str],
) -> Optional[AppUser]:
    """Authenticate a Subsonic client against the DEDICATED token only.

    The panel bcrypt password is deliberately NOT accepted here anymore:
    it used to travel in the query string (`p=<panel password>`), which leaks
    the main account credential into proxy logs and browser history.
    """
    if not username:
        return None
    user = db.scalar(select(AppUser).where(AppUser.username == username))
    if not user or not user.is_active:
        verify_password(password or "x", _DUMMY_HASH)
        return None
    if user.locked_until and user.locked_until > utcnow():
        verify_password(password or "x", user.password_hash)
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
        _record_success(db, user)
        return user
    _record_failure(db, user)
    return None
