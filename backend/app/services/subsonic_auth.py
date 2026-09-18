"""
T-Music Subsonic auth helpers.
Supports:
  - plaintext password (p=) verified against bcrypt app_users.password_hash
  - dedicated Subsonic token as password (p=token)
  - Subsonic token auth t=md5(token+salt), s=salt
Token is stored Fernet-encrypted on app_users.subsonic_token_enc.
"""
import base64
import hashlib
import hmac
import logging
import secrets
from typing import Optional

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import verify_password
from app.models.auth import AppUser

logger = logging.getLogger("tmusic.subsonic")


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


def authenticate_subsonic(
    db: Session,
    username: Optional[str],
    password: Optional[str],
    token: Optional[str],
    salt: Optional[str],
) -> Optional[AppUser]:
    if not username:
        return None
    user = db.scalar(select(AppUser).where(AppUser.username == username))
    if not user or not user.is_active:
        # timing equalizer for unknown user
        verify_password(password or "x", "$2b$12$" + "A" * 53)
        return None

    if password:
        if verify_password(password, user.password_hash):
            return user
        shared = decrypt_token(user.subsonic_token_enc)
        if shared and hmac.compare_digest(password, shared):
            return user

    if token and salt:
        shared = decrypt_token(user.subsonic_token_enc)
        if shared:
            expected = hashlib.md5((shared + salt).encode("utf-8")).hexdigest()
            if hmac.compare_digest(token.lower(), expected.lower()):
                return user

    return None
