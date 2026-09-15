"""
T-Music Security & Cryptography Module
Implements industry-standard authentication, password/PIN hashing, and JWT tokens.

Notes on hardening (2026-09 security audit):
- JWT via PyJWT (python-jose is unmaintained; CVE-2024-33663/33664).
- Unsalted SHA-256 password/PIN hashes are NOT accepted anymore.
  Only the SECRET_KEY-salted legacy variant is tolerated for migration,
  and successful logins are auto-upgraded to bcrypt by the auth service.
"""
import uuid
import hmac
import hashlib
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Optional, Dict, Any

import jwt
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext

from app.core.config import settings

logger = logging.getLogger(__name__)

# Password & PIN hashing context (bcrypt with automatic upgrade)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a password using strong salted bcrypt."""
    return pwd_context.hash(password)


@lru_cache(maxsize=1)
def timing_equalizer_hash() -> str:
    """
    Constant bcrypt hash burned when a username does not exist.
    Equalizes response time between 'no such user' and 'wrong password'
    to prevent username enumeration via timing analysis.
    """
    return pwd_context.hash("timing-equalizer-dummy-password")


def _legacy_salted_sha256(secret_value: str) -> str:
    """Pre-bcrypt hash (SECRET_KEY-salted SHA-256). Verify-only for migration."""
    salted = f"{settings.SECRET_KEY}:{secret_value}".encode("utf-8")
    return hashlib.sha256(salted).hexdigest()


def is_legacy_hash(stored_hash: str) -> bool:
    """Legacy 64-hex SHA-256 variant (salted) — must be upgraded to bcrypt on next login."""
    return bool(stored_hash) and not stored_hash.startswith("$") and len(stored_hash) == 64


def is_legacy_pin_hash(stored_hash: str) -> bool:
    return is_legacy_hash(stored_hash)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password in constant time against bcrypt hash or SECRET_KEY-salted legacy SHA-256."""
    if not plain_password or not hashed_password:
        return False
    if hashed_password.startswith("$"):
        try:
            return pwd_context.verify(plain_password, hashed_password)
        except Exception:
            return False
    # Legacy migration path — salted only. Unsalted SHA-256 is rejected.
    if is_legacy_hash(hashed_password):
        return hmac.compare_digest(_legacy_salted_sha256(plain_password), hashed_password)
    return False


def hash_pin(pin: str) -> str:
    """Hash a numeric PIN with bcrypt (slow, salted)."""
    return pwd_context.hash(pin)


def verify_pin(plain_pin: str, stored_hash: str) -> bool:
    """Verify a PIN code in constant time (bcrypt or SECRET_KEY-salted legacy SHA-256)."""
    if not plain_pin or not stored_hash:
        return False
    if stored_hash.startswith("$"):
        try:
            return pwd_context.verify(plain_pin, stored_hash)
        except Exception:
            return False
    # Legacy migration path — salted only. Unsalted SHA-256 is rejected.
    if is_legacy_hash(stored_hash):
        return hmac.compare_digest(_legacy_salted_sha256(plain_pin), stored_hash)
    return False


def create_access_token(
    subject: str,
    claims: Optional[Dict[str, Any]] = None,
    expires_delta: Optional[timedelta] = None
) -> tuple[str, str, datetime]:
    """
    Generate a signed JWT access token.
    Returns: (token_str, jti, expire_datetime)
    """
    now = datetime.utcnow()
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    jti = str(uuid.uuid4())

    to_encode: Dict[str, Any] = {
        "sub": subject,
        "jti": jti,
        "iat": now,
        "exp": expire,
    }
    if claims:
        to_encode.update(claims)

    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt, jti, expire


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Decode and validate a JWT access token (signature + exp)."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except InvalidTokenError:
        return None


def token_expiry_from_payload(payload: Dict[str, Any]) -> datetime:
    exp = payload.get("exp")
    if isinstance(exp, (int, float)):
        return datetime.utcfromtimestamp(exp)
    return datetime.utcnow() + timedelta(days=1)
