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
from app.main import _safe_log_path, _safe_log_query
from app.services.cover_cache_service import _ip_allowed


def test_sensitive_query_values_are_redacted():
    query = _safe_log_query("u=admin&p=secret&t=digest&s=salt&token=jwt&v=1.16.1")
    assert "secret" not in query
    assert "digest" not in query
    assert "salt" not in query
    assert "jwt" not in query
    assert "u=admin" in query
    assert _safe_log_path("/s/share-secret") == "/s/[redacted]"


def test_ssrf_blocks_non_global_addresses():
    assert _ip_allowed("100.64.0.1", "cgnat") is False
    assert _ip_allowed("100.100.100.200", "metadata") is False
    assert _ip_allowed("::ffff:100.64.0.1", "mapped") is False
    assert _ip_allowed("8.8.8.8", "public") is True


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
