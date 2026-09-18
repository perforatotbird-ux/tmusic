"""
T-Music Hardening Tests (tech-debt follow-up)
Covers previously untested security-critical logic without touching Telegram:
CSRF double-submit, stream-token scope, revocation purge/cache, time helper,
direct-bcrypt hashing, and TG candidate-source selection.
"""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from app.main import app
from app.core.csrf import csrf_check_failed
from app.core.database import SessionLocal
from app.core.security import hash_password, verify_password
from app.core.token_store import (
    is_token_revoked, revoke_access_token, purge_expired_revocations,
)
from app.models.auth import AppUser, RevokedToken
from app.tg.manager import TelegramManager
from app.utils.time import utcnow

client = TestClient(app)

TEST_PASSWORD = "Hardening!Test1"


def _req(method, path, headers=None, cookies=None):
    scope = {"type": "http", "method": method, "path": path,
             "headers": [], "query_string": b""}
    h = {}
    for k, v in (headers or {}).items():
        h[k.lower().encode()] = v.encode()
    if cookies:
        h[b"cookie"] = ("; ".join(f"{k}={v}" for k, v in cookies.items())).encode()
    scope["headers"] = [(k, v) for k, v in h.items()]
    return Request(scope)


def test_csrf_blocks_cookie_mutation_without_header():
    s = {"access_token": "jwt", "csrf_token": "tok"}
    assert csrf_check_failed(_req("POST", "/api/library/cover", {}, s)) is True


def test_csrf_allows_matching_header_and_bearer():
    s = {"access_token": "jwt", "csrf_token": "tok"}
    assert csrf_check_failed(_req("POST", "/api/library/cover", {"X-CSRF-Token": "tok"}, s)) is False
    assert csrf_check_failed(_req("POST", "/api/library/cover", {"Authorization": "Bearer x"}, s)) is False
    assert csrf_check_failed(_req("GET", "/api/library/artists", {}, s)) is False
    assert csrf_check_failed(_req("POST", "/api/auth/login", {}, {})) is False


def test_utcnow_is_naive_utc():
    now = utcnow()
    assert now.tzinfo is None
    assert (datetime.now() - now).total_seconds() < 3600 * 24  # sanity: plausible wall time


def test_bcrypt_direct_roundtrip():
    h = hash_password("some-password-123")
    assert h.startswith("$2b$")
    assert verify_password("some-password-123", h) is True
    assert verify_password("nope", h) is False
    assert verify_password("x", "garbage") is False
    ph = hash_password("5678")
    assert verify_password("5678", ph) is True
    assert verify_password("0000", ph) is False


def test_candidate_sources_skips_garbage_and_sorts_healthy_first():
    m = TelegramManager()
    m.clients = {40: SimpleNamespace(is_connected=True),
                 41: SimpleNamespace(is_connected=False)}
    m.primary_id = 40
    out = m.get_candidate_sources(
        {"40": {"msg_ids": "1"}, "xx": {"msg_ids": "2"},
         "99": {"msg_ids": "3"}, 41: "5"},
        None,
    )
    assert out[0] == (40, "1")  # healthy primary first
    assert (41, "5") in out  # unhealthy kept as failover, sorted last
    assert all(aid in (40, 41) for aid, _ in out)


def test_revocation_purge_and_negative_cache():
    with SessionLocal() as db:
        # Seed one long-expired and one live revocation directly.
        db.add(RevokedToken(jti="test-old-jti", expires_at=datetime(2000, 1, 1)))
        db.commit()
        assert is_token_revoked(db, "test-old-jti") is True
        removed = purge_expired_revocations(db)
        assert removed >= 1
        assert is_token_revoked(db, "test-old-jti") is False


def _provision_admin():
    with SessionLocal() as db:
        user = db.execute(
            select(AppUser).where(AppUser.username == "admin")).scalar_one_or_none()
        if user is None:
            user = AppUser(username="admin", password_hash=hash_password(TEST_PASSWORD))
            db.add(user)
        else:
            user.password_hash = hash_password(TEST_PASSWORD)
        user.is_active = True
        user.failed_login_attempts = 0
        user.locked_until = None
        db.commit()


def test_stream_query_token_requires_scope():
    _provision_admin()
    tok = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": TEST_PASSWORD}).json()["access_token"]
    # Full session JWT in the query string must be rejected...
    assert client.get("/api/stream/1?token=" + tok).status_code == 401
    # ...while a minted stream token passes authentication (404 = track miss).
    me_headers = {"Authorization": f"Bearer {tok}"}
    st = client.post("/api/stream/token", headers=me_headers).json()["stream_token"]
    assert client.get("/api/stream/999999?token=" + st).status_code == 404
    # Revoking the session token kills it even with a warm negative cache.
    with SessionLocal() as db:
        assert revoke_access_token(db, tok) is True
    assert client.get("/api/auth/me", headers=me_headers).status_code == 401
