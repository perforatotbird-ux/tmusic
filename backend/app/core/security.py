"""Password migration and strict JWT validation.

New hashes: Argon2id. Read compatibility: bounded bcrypt and temporary
SECRET_KEY-salted SHA-256. No unsalted SHA-256 support.
"""
import uuid
import hmac
import hashlib
import logging
import re
import threading
import base64
import binascii
from datetime import datetime, timedelta
from calendar import timegm
from functools import lru_cache
from typing import Optional, Dict, Any

import bcrypt
import jwt
from jwt.exceptions import InvalidTokenError
from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings
from app.utils.time import utcnow, utcfromtimestamp

logger = logging.getLogger(__name__)
MAX_PASSWORD_BYTES = 4096  # covers historical CLI passwords; bounded input
_PASSWORD_SLOTS = threading.BoundedSemaphore(settings.PASSWORD_HASH_CONCURRENCY)
_ARGON2 = PasswordHasher(
    time_cost=settings.PASSWORD_ARGON2_TIME_COST,
    memory_cost=settings.PASSWORD_ARGON2_MEMORY_KIB,
    parallelism=settings.PASSWORD_ARGON2_PARALLELISM,
    hash_len=32, salt_len=16, type=Type.ID,
)
_BCRYPT_RE = re.compile(r"^\$2[aby]\$(\d{2})\$[./A-Za-z0-9]{53}$")
_ARGON2_RE = re.compile(
    r"^\$argon2id\$v=19\$m=\d+,t=\d+,p=\d+\$([A-Za-z0-9+/]+)\$([A-Za-z0-9+/]+)$"
)
_LEGACY_RE = re.compile(r"^[0-9a-f]{64}$")
JWT_REQUIRED_CLAIMS = ("sub", "exp", "iat", "jti", "uid", "tv")
_JWT_RESERVED = frozenset({"sub", "exp", "iat", "jti"})


def _password_bytes(password: str) -> Optional[bytes]:
    if not isinstance(password, str) or not password:
        return None
    try:
        data = password.encode("utf-8")
    except UnicodeError:
        return None
    return data if len(data) <= MAX_PASSWORD_BYTES else None


def is_bcrypt_hash(value: str) -> bool:
    if not isinstance(value, str):
        return False
    match = _BCRYPT_RE.fullmatch(value)
    return bool(match and 4 <= int(match.group(1)) <= 16)


def _safe_argon2_hash(value: str) -> bool:
    if not isinstance(value, str) or not value.startswith("$argon2id$") or len(value) > 255:
        return False
    try:
        match = _ARGON2_RE.fullmatch(value)
        if not match:
            return False
        for encoded in match.groups():
            decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
            if base64.b64encode(decoded).decode("ascii").rstrip("=") != encoded:
                return False
        params = extract_parameters(value)
        return (params.type == Type.ID and params.version == 19
                and 8192 <= params.memory_cost <= 131072
                and 1 <= params.time_cost <= 6 and 1 <= params.parallelism <= 4
                and 16 <= params.hash_len <= 64 and 8 <= params.salt_len <= 64)
    except (InvalidHashError, ValueError, TypeError, binascii.Error):
        return False


def is_supported_password_hash(value: str) -> bool:
    """Bootstrap accepts modern Argon2id or valid bounded bcrypt, not legacy SHA."""
    return _safe_argon2_hash(value) or is_bcrypt_hash(value)


def hash_password(password: str) -> str:
    if _password_bytes(password) is None:
        raise ValueError("Invalid password length or encoding.")
    with _PASSWORD_SLOTS:
        return _ARGON2.hash(password)


@lru_cache(maxsize=1)
def timing_equalizer_hash() -> str:
    return hash_password("timing-equalizer-dummy-password")


def _legacy_salted_sha256(password: str) -> str:
    return hashlib.sha256(f"{settings.SECRET_KEY}:{password}".encode("utf-8")).hexdigest()


def is_legacy_hash(value: str) -> bool:
    return isinstance(value, str) and bool(_LEGACY_RE.fullmatch(value))


def verify_password(password: str, stored_hash: str) -> bool:
    data = _password_bytes(password)
    if data is None or not isinstance(stored_hash, str):
        return False
    try:
        if _safe_argon2_hash(stored_hash):
            with _PASSWORD_SLOTS:
                return _ARGON2.verify(stored_hash, password)
        if is_bcrypt_hash(stored_hash):
            # Legacy read only. Ambiguous 72-byte passwords get a restricted
            # password-change session; never rehash their suffix silently.
            with _PASSWORD_SLOTS:
                return bcrypt.checkpw(data[:72], stored_hash.encode("ascii"))
        if is_legacy_hash(stored_hash):
            return hmac.compare_digest(_legacy_salted_sha256(password), stored_hash)
    except (VerificationError, InvalidHashError, ValueError, TypeError, UnicodeError):
        return False
    return False


def password_change_required(password: str, stored_hash: str) -> bool:
    data = _password_bytes(password)
    return bool(data is not None and is_bcrypt_hash(stored_hash) and len(data) >= 72)


def password_needs_rehash(stored_hash: str) -> bool:
    if _safe_argon2_hash(stored_hash):
        return _ARGON2.check_needs_rehash(stored_hash)
    return is_bcrypt_hash(stored_hash) or is_legacy_hash(stored_hash)


def _valid_jwt_claims(payload: Dict[str, Any]) -> bool:
    return (isinstance(payload.get("sub"), str) and 0 < len(payload["sub"]) <= 64
            and isinstance(payload.get("jti"), str) and 0 < len(payload["jti"]) <= 128
            and type(payload.get("uid")) is int and payload["uid"] > 0
            and type(payload.get("tv")) is int and payload["tv"] >= 0
            and type(payload.get("iat")) is int and payload["iat"] > 0
            and type(payload.get("exp")) is int and payload["iat"] < payload["exp"] <= 253402300799
            and ("scope" not in payload or payload["scope"] == "stream")
            and ("pwd_reset" not in payload or type(payload["pwd_reset"]) is bool))


def create_access_token(subject: str, claims: Optional[Dict[str, Any]] = None,
                        expires_delta: Optional[timedelta] = None) -> tuple[str, str, datetime]:
    supplied = dict(claims or {})
    if _JWT_RESERVED.intersection(supplied):
        raise ValueError("Reserved JWT claims cannot be overridden.")
    now = utcnow()
    expire = now + (expires_delta if expires_delta is not None else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    jti = str(uuid.uuid4())
    payload = {**supplied, "sub": subject, "jti": jti,
               "iat": timegm(now.utctimetuple()), "exp": timegm(expire.utctimetuple())}
    if not _valid_jwt_claims(payload):
        raise ValueError("Invalid JWT claims.")
    encoded = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded, jti, expire


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM],
                             options={"require": list(JWT_REQUIRED_CLAIMS)})
        return payload if _valid_jwt_claims(payload) else None
    except (InvalidTokenError, ValueError, TypeError, OverflowError):
        return None


def token_expiry_from_payload(payload: Dict[str, Any]) -> datetime:
    if type(payload.get("exp")) is not int or not 0 < payload["exp"] <= 253402300799:
        raise ValueError("Invalid token expiry.")
    return utcfromtimestamp(payload["exp"])
