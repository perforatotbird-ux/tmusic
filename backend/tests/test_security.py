"""
Tests for T-Music Security & Cryptography
"""
import sys
from pathlib import Path

# Add backend to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import (
    hash_password, verify_password,
    create_access_token, decode_access_token
)
from app.core.rate_limiter import SlidingWindowRateLimiter


def test_password_hashing():
    pw = "SuperSecret123!"
    hashed = hash_password(pw)
    assert hashed != pw
    assert verify_password(pw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_jwt_lifecycle():
    token, jti, expire = create_access_token("test_user", claims={"role": "listener"})
    assert isinstance(token, str)

    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "test_user"
    assert payload["jti"] == jti
    assert payload["role"] == "listener"

    invalid = decode_access_token(token + "tampered")
    assert invalid is None


def test_rate_limiter_lockout():
    limiter = SlidingWindowRateLimiter()
    ip = "192.0.2.1"

    locked, _ = limiter.is_locked_out(ip)
    assert locked is False

    # Simulate 5 failed attempts
    for i in range(4):
        is_locked, left = limiter.record_failure(ip, max_attempts=5, lockout_seconds=10)
        assert is_locked is False

    is_locked, _ = limiter.record_failure(ip, max_attempts=5, lockout_seconds=10)
    assert is_locked is True

    locked, remaining = limiter.is_locked_out(ip)
    assert locked is True
    assert remaining > 0

    # Reset on success
    limiter.record_success(ip)
    locked, _ = limiter.is_locked_out(ip)
    assert locked is False
