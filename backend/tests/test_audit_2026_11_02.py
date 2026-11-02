"""Regression tests for the 2026-11-02 audit fixes (H-1, M-1..M-6, B-1).

Isolated SQLite DB for AppUser; no network, Telegram or live PostgreSQL rows.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api import auth_router, subsonic_router
from app.core import body_limits
from app.core.config import insecure_default_warnings, settings
from app.core.login_device import device_id_for, issue_device_token
from app.core.rate_limiter import limiter
from app.core.security import hash_password
from app.core.user_cache import get_user_by_username, invalidate_user
from app.models.auth import AppUser
from app.schemas.auth import TokenResponse
from app.services import subsonic_auth
from app.services.auth_service import GENERIC_LOGIN_ERROR, AuthService
from app.services.discovery_service import DISCOVERY_MAGIC_REQUEST, DiscoveryDatagramProtocol
from app.utils.client_ip import cookie_secure, get_client_ip

PASSWORD = "correct horse battery"


@pytest.fixture
def auth_db(monkeypatch):
    monkeypatch.setattr(settings, "SESSION_SECRET", "audit-2026-11-02-test-secret")
    monkeypatch.setattr(settings, "MAX_FAILED_LOGIN_ATTEMPTS", 3)
    engine = create_engine("sqlite:///:memory:")
    AppUser.__table__.create(engine)
    with Session(engine) as db:
        user = AppUser(username="owner", password_hash=hash_password(PASSWORD), role="user", is_active=True)
        db.add(user)
        db.commit()
        invalidate_user()
        yield db, user
        invalidate_user()
    engine.dispose()


def _login(db, password, ip, device=None, username="owner"):
    return AuthService.authenticate_by_password(db, username, password, ip, device)


# ─── H-1: anonymous lockout DoS + enumeration ───────────────────────────────

def _lock_account_from(db, ips):
    for ip in ips:
        token, err = _login(db, "wrong", ip)
        assert token is None and err == GENERIC_LOGIN_ERROR


def test_known_device_logs_in_while_account_locked_by_attacker(auth_db):
    db, user = auth_db
    first, err = _login(db, PASSWORD, "10.0.0.2")
    assert err is None and first.device_token
    _lock_account_from(db, ["203.0.113.1", "203.0.113.2", "203.0.113.3"])
    db.refresh(user)
    assert user.locked_until is not None

    # Untrusted client (even with the right password) is still locked out ...
    token, err = _login(db, PASSWORD, "198.51.100.7")
    assert token is None and err == GENERIC_LOGIN_ERROR
    # ... but the owner's remembered browser is not.
    token, err = _login(db, PASSWORD, "198.51.100.8", device=first.device_token)
    assert err is None and token.access_token


def test_locked_and_unknown_accounts_answer_identically(auth_db):
    db, _ = auth_db
    _lock_account_from(db, ["203.0.113.1", "203.0.113.2", "203.0.113.3"])
    _, locked_err = _login(db, PASSWORD, "198.51.100.1")
    _, unknown_err = _login(db, PASSWORD, "198.51.100.2", username="nobody")
    _, wrong_err = _login(db, "nope", "198.51.100.3", username="owner")
    assert locked_err == unknown_err == wrong_err == GENERIC_LOGIN_ERROR


def test_device_failures_lock_only_that_device(auth_db):
    db, user = auth_db
    first, _ = _login(db, PASSWORD, "10.0.0.2")
    for _ in range(3):
        assert _login(db, "wrong", "10.0.0.2", device=first.device_token)[0] is None
    # Device is locked now, even with the right password.
    assert _login(db, PASSWORD, "10.0.0.2", device=first.device_token)[0] is None
    db.refresh(user)
    # The account itself was not touched by device-scoped failures.
    assert not user.locked_until and not user.failed_login_attempts
    limiter.record_success("10.0.0.2")
    assert _login(db, PASSWORD, "10.0.0.9")[0] is not None


def test_device_token_bound_to_user_and_token_version(auth_db):
    _, user = auth_db
    tok = issue_device_token(user.id, 0)
    assert device_id_for(tok, user.id, 0)
    assert device_id_for(tok, user.id, 1) is None  # password change / revoke-all
    assert device_id_for(tok, user.id + 1, 0) is None
    assert device_id_for(tok[:-2] + "xx", user.id, 0) is None
    assert device_id_for("garbage", user.id, 0) is None


def test_device_token_never_serialized_in_login_body():
    resp = TokenResponse(access_token="a", token_type="bearer", expires_in=1, device_token="secret")
    assert "device_token" not in resp.model_dump()
    assert "secret" not in resp.model_dump_json()


# ─── B-1: avatar changes persist ────────────────────────────────────────────

def test_reset_avatar_persists_for_detached_cached_user(auth_db):
    db, user = auth_db
    user.avatar_key = "custom:abc"
    db.commit()
    invalidate_user()
    detached = get_user_by_username(db, "owner")
    assert detached not in db
    asyncio.run(auth_router.reset_avatar(db=db, user=detached))
    stored = db.scalar(select(AppUser).where(AppUser.username == "owner"))
    db.refresh(stored)
    assert stored.avatar_key is None
    assert stored.avatar_version == 1
    assert get_user_by_username(db, "owner").avatar_key is None  # cache invalidated


# ─── M-2: X-Forwarded-For / scheme handling ─────────────────────────────────

def _req(headers=None, peer="172.18.0.1", scheme="http"):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw,
                    "client": (peer, 1234), "scheme": scheme, "server": ("x", 80),
                    "query_string": b""})


def test_client_ip_uses_proxy_appended_entry(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", [])
    spoofed = _req({"X-Forwarded-For": "1.2.3.4, 198.51.100.20"})
    assert get_client_ip(spoofed) == "198.51.100.20"
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2)
    assert get_client_ip(spoofed) == "1.2.3.4"
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    assert get_client_ip(_req({"X-Forwarded-For": "not-an-ip"})) == "172.18.0.1"


def test_proxy_headers_ignored_from_untrusted_peer(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", ["127.0.0.1/32"])
    assert get_client_ip(_req({"X-Forwarded-For": "9.9.9.9"}, peer="172.18.0.1")) == "172.18.0.1"
    assert get_client_ip(_req({"X-Forwarded-For": "9.9.9.9"}, peer="127.0.0.1")) == "9.9.9.9"
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    assert get_client_ip(_req({"X-Forwarded-For": "9.9.9.9"}, peer="127.0.0.1")) == "127.0.0.1"


def test_cookie_secure_auto_follows_https(monkeypatch):
    monkeypatch.setattr(settings, "COOKIE_SECURE", None)
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_IPS", [])
    assert cookie_secure(_req()) is False
    assert cookie_secure(_req(scheme="https")) is True
    assert cookie_secure(_req({"X-Forwarded-Proto": "https"})) is True
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    assert cookie_secure(_req(scheme="https")) is False


def test_production_warns_about_insecure_defaults():
    cfg = SimpleNamespace(ENVIRONMENT="production", TRUSTED_HOSTS=["*"], COOKIE_SECURE=False,
                          CORS_ALLOW_LAN=False, TRUST_PROXY_HEADERS=False, TRUSTED_PROXY_IPS=[])
    text = " ".join(insecure_default_warnings(cfg))
    assert "TRUSTED_HOSTS" in text and "COOKIE_SECURE" in text
    cfg.ENVIRONMENT = "development"
    assert insecure_default_warnings(cfg) == []


# ─── M-5: Subsonic ──────────────────────────────────────────────────────────

def test_subsonic_disabled_flag_is_enforced(monkeypatch):
    monkeypatch.setattr(settings, "SUBSONIC_ENABLED", False)
    with pytest.raises(HTTPException) as err:
        asyncio.run(subsonic_router.subsonic_entry("ping.view", _req(), db=None))
    assert err.value.status_code == 404


def test_subsonic_get_user_reflects_real_role(monkeypatch):
    regular = SimpleNamespace(id=5, username="bob", role="user")
    monkeypatch.setattr(subsonic_router, "_http_auth_user", lambda *a: regular)
    response = asyncio.run(subsonic_router._handle("getUser", None, None, {}, "json"))
    body = json.loads(response.body)["subsonic-response"]["user"]
    assert body["adminRole"] is False and body["settingsRole"] is False


def test_subsonic_ignores_web_lockout_and_needs_no_argon(auth_db, monkeypatch):
    db, user = auth_db
    token = subsonic_auth.ensure_subsonic_token(db, user)
    _lock_account_from(db, ["203.0.113.1", "203.0.113.2", "203.0.113.3"])
    assert subsonic_auth.authenticate_subsonic(db, "owner", token, None, None, "10.0.0.5") is not None
    # Unknown users take the same cheap path: no Argon2 verify at all.
    import app.core.security as security
    monkeypatch.setattr(security, "verify_password", lambda *a: pytest.fail("argon2 used"))
    assert subsonic_auth.authenticate_subsonic(db, "nobody", token, None, None, "10.0.0.6") is None


# ─── M-3: discovery ─────────────────────────────────────────────────────────

class _Transport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append(addr)


def test_discovery_ignores_public_sources_and_rate_limits():
    proto = DiscoveryDatagramProtocol(http_port=8005, project_name="T-Music")
    transport = _Transport()
    proto.connection_made(transport)
    proto.datagram_received(DISCOVERY_MAGIC_REQUEST.encode(), ("8.8.8.8", 5000))
    assert transport.sent == []
    for _ in range(50):
        proto.datagram_received(DISCOVERY_MAGIC_REQUEST.encode(), ("192.168.1.77", 5000))
    assert 0 < len(transport.sent) <= 10


def test_discovery_disabled_by_default():
    from app.core.config import Settings
    assert Settings.model_fields["DISCOVERY_ENABLED"].default is False


# ─── M-1: large uploads are authorized before the body is read ──────────────

def _large_scope():
    return {"type": "http", "method": "POST", "path": "/api/admin/vfs/import/upload",
            "headers": [(b"content-length", b"10")]}


def test_large_upload_rejected_before_reading_body(monkeypatch):
    async def deny(scope, path):
        return 401, "Authentication required."
    monkeypatch.setattr(body_limits, "preauthorize_large_upload", deny)
    reads, sent, called = [], [], []

    async def receive():
        reads.append(1)
        return {"type": "http.request", "body": b"x" * 10, "more_body": False}

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        called.append(1)

    asyncio.run(body_limits.BodySizeLimitMiddleware(downstream)(_large_scope(), receive, send))
    assert reads == [] and called == []
    assert sent[0]["status"] == 401


def test_large_upload_concurrency_cap(monkeypatch):
    async def allow(scope, path):
        return None
    monkeypatch.setattr(body_limits, "preauthorize_large_upload", allow)
    monkeypatch.setattr(body_limits, "_large_uploads_in_flight", body_limits.MAX_CONCURRENT_LARGE_UPLOADS)
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        raise AssertionError("body must not be read")

    asyncio.run(body_limits.BodySizeLimitMiddleware(None)(_large_scope(), receive, send))
    assert sent[0]["status"] == 429


def test_large_upload_real_authorizer_rejects_anonymous():
    denied = asyncio.run(body_limits.preauthorize_large_upload(
        {**_large_scope(), "query_string": b"", "server": ("x", 80), "scheme": "http"},
        "/api/admin/vfs/import/upload"))
    assert denied and denied[0] == 401
