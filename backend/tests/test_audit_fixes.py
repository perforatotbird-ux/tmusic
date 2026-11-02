"""
Tests for the 2026-09 security-audit fixes:
- CSRF fail-closed behaviour (csrf_check_failed)
- Share default TTL (create_share_impl)
- One-shot ADMIN_PASSWORD_HASH bootstrap
- Subsonic auth accepts only the dedicated token
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from types import SimpleNamespace

from fastapi import HTTPException

from app.core.csrf import csrf_check_failed


def _request(method="POST", path="/api/playlists", cookies=None, headers=None):
    # Starlette Headers are case-insensitive; mimic with lowercase keys.
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        cookies=cookies or {},
        headers=headers,
    )


def test_csrf_safe_methods_pass():
    assert csrf_check_failed(_request(method="GET")) is False
    assert csrf_check_failed(_request(method="HEAD")) is False
    assert csrf_check_failed(_request(method="OPTIONS")) is False


def test_csrf_non_api_paths_pass():
    assert csrf_check_failed(_request(path="/rest/ping")) is False


def test_csrf_exempt_paths_pass():
    for path in ("/api/auth/login", "/api/auth/logout", "/api/auth/csrf"):
        assert csrf_check_failed(_request(path=path, cookies={"access_token": "x"})) is False


def test_csrf_bearer_requests_pass_without_cookie():
    # Bearer-authenticated requests are not cookie-authenticated: no CSRF surface.
    assert csrf_check_failed(
        _request(headers={"Authorization": "Bearer abc"}, cookies={"access_token": "x"})
    ) is False


def test_csrf_no_cookie_session_no_check():
    # No access_token cookie -> nothing to protect.
    assert csrf_check_failed(_request()) is False


def test_csrf_fail_closed_without_csrf_cookie():
    # KEY FIX: cookie-authenticated mutation with NO csrf cookie must fail now.
    assert csrf_check_failed(
        _request(cookies={"access_token": "session-jwt"})
    ) is True


def test_csrf_mismatched_header_fails():
    assert csrf_check_failed(
        _request(
            cookies={"access_token": "jwt", "csrf_token": "good"},
            headers={"X-CSRF-Token": "evil"},
        )
    ) is True


def test_csrf_matching_header_passes():
    assert csrf_check_failed(
        _request(
            cookies={"access_token": "jwt", "csrf_token": "good"},
            headers={"X-CSRF-Token": "good"},
        )
    ) is False


# ─── 2026-09-29 remediation round ────────────────────────────────────────────

def test_csrf_garbage_authorization_header_does_not_skip_check():
    """L-1: only a well-formed `Bearer <token>` skips the CSRF check.

    Any other Authorization value (garbage, wrong scheme, bare/empty
    Bearer) used to skip the check while auth silently fell back to the
    victim's cookie — a CSRF bypass for preflight-passing origins.
    """
    from app.core.csrf import has_bearer_credentials

    base_cookies = {"access_token": "jwt", "csrf_token": "tok"}
    for bad in ("garbage", "Basic abc", "Bearer", "Bearer ", "Bearer   "):
        req = _request(cookies=base_cookies, headers={"Authorization": bad})
        assert has_bearer_credentials(req) is False, bad
        assert csrf_check_failed(req) is True, bad
    for good in ("Bearer abc", "BEARER abc", "bearer abc"):
        req = _request(cookies=base_cookies, headers={"Authorization": good})
        assert has_bearer_credentials(req) is True, good
        assert csrf_check_failed(req) is False, good


def test_transcode_src_ext_allowlist():
    """M-5: temp-file extension is allowlisted — traversal names collapse
    to "audio" instead of escaping TRANSCODE_CACHE_DIR."""
    from app.services.transcoding_service import safe_src_ext

    assert safe_src_ext("song.flac") == "flac"
    assert safe_src_ext("SONG.MP4") == "mp4"
    assert safe_src_ext("track.opus") == "opus"
    assert safe_src_ext("noext") == "audio"
    assert safe_src_ext("x.mp3/../../evil") == "audio"
    assert safe_src_ext("a.mp3/..\\..\\win") == "audio"
    assert safe_src_ext("song.mp3 ") == "audio"  # trailing space: unsafe on Windows
    assert safe_src_ext("archive.tar.gz") == "gz"  # multi-dot names still work
    assert safe_src_ext("UPPER.FLAC") == "flac"
    assert safe_src_ext(".hidden") == "hidden"  # harmless dotted name, stays in-dir
    assert safe_src_ext("") == "audio"
    assert safe_src_ext(None) == "audio"


# ─── Share default TTL ─────────────────────────────────────────────────────────

def test_share_default_ttl_applied(monkeypatch):
    import app.api.share_router as sr
    from datetime import datetime, timedelta

    stored = {}

    class FakeQuery:
        def __init__(self, **kw):
            stored.update(kw)

    class FakeShare(FakeQuery):
        pass

    class FakeDB:
        def add(self, obj):
            pass

        def commit(self):
            pass

        def refresh(self, obj):
            obj.id = 1

    monkeypatch.setattr(sr, "ShareLink", FakeShare)
    monkeypatch.setattr(sr.secrets, "token_urlsafe", lambda n: "tok")
    monkeypatch.setattr(sr.settings, "SHARE_DEFAULT_TTL_HOURS", 24 * 30)

    share = sr.create_share_impl(FakeDB(), user_id=1, file_id=2)

    assert stored.get("token") == "tok"
    # expires_at set to ~30 days ahead
    assert share.expires_at is not None
    delta = share.expires_at - datetime.utcnow()
    assert timedelta(days=29) < delta <= timedelta(days=30)


def test_share_explicit_zero_is_permanent(monkeypatch):
    import app.api.share_router as sr

    class FakeDB:
        def add(self, obj):
            pass

        def commit(self):
            pass

        def refresh(self, obj):
            obj.id = 1

    class FakeShare:
        def __init__(self, **kwargs):
            self.expires_at = None  # ORM default
            self.__dict__.update(kwargs)

    monkeypatch.setattr(sr, "ShareLink", FakeShare)
    monkeypatch.setattr(sr.secrets, "token_urlsafe", lambda n: "tok")

    share = sr.create_share_impl(FakeDB(), user_id=1, file_id=2, expires_in_hours=0)
    assert share.expires_at is None


# ─── One-shot admin bootstrap ────────────────────────────────────────────────

def test_bootstrap_preserves_panel_password_change():
    """Env hash must NOT re-apply when panel changed the password meanwhile."""
    import app.services.bootstrap as bs
    from datetime import datetime

    import bcrypt
    env_hash = bcrypt.hashpw(b"synthetic-env", bcrypt.gensalt(rounds=4)).decode()
    panel_hash = bcrypt.hashpw(b"synthetic-panel", bcrypt.gensalt(rounds=4)).decode()

    user = SimpleNamespace(
        username="admin",
        password_hash=panel_hash,
        is_active=True,
        failed_login_attempts=0,
        locked_until=None,
        token_version=3,
        updated_at=datetime.utcnow(),
    )

    class FakeDB:
        def __init__(self):
            self.settings_store = {_APPLIED: env_hash}

        def execute(self, q):
            return SimpleNamespace(scalar_one_or_none=lambda: user)

        def get(self, model, key):
            if key == _APPLIED:
                return SimpleNamespace(key=key, value=self.settings_store[key])

        def add(self, obj):
            self.settings_store[obj.key] = obj.value

        def commit(self):
            pass

    _APPLIED = bs._APPLIED_HASH_KEY
    db = FakeDB()

    import app.core.config as cfg
    original = cfg.settings.ADMIN_PASSWORD_HASH
    try:
        cfg.settings.ADMIN_PASSWORD_HASH = env_hash
        bs.ensure_admin_bootstrap(db)
        # Panel-side password preserved, no session revocation
        assert user.password_hash == panel_hash
        assert user.token_version == 3
    finally:
        cfg.settings.ADMIN_PASSWORD_HASH = original


def test_bootstrap_does_not_reapply_changed_env_hash():
    """Owner decision 2026-10-02: the panel password is fixed at "123".

    A changed env hash is logged and IGNORED. Re-applying would silently
    overwrite whatever the panel holds and revoke sessions for a rotation the
    owner never asked for.
    """
    import app.services.bootstrap as bs
    from datetime import datetime

    import bcrypt
    old_env_hash = bcrypt.hashpw(b"synthetic-old", bcrypt.gensalt(rounds=4)).decode()
    new_env_hash = bcrypt.hashpw(b"synthetic-new", bcrypt.gensalt(rounds=4)).decode()

    user = SimpleNamespace(
        username="admin",
        password_hash=old_env_hash,
        is_active=True,
        failed_login_attempts=0,
        locked_until=None,
        token_version=0,
        updated_at=datetime.utcnow(),
    )

    _APPLIED = bs._APPLIED_HASH_KEY

    class WriteThroughRow:
        """Mimics a persistent ORM row: attribute writes hit the store."""

        def __init__(self, store, key, value):
            self._store = store
            self.key = key
            self._value = value

        @property
        def value(self):
            return self._value

        @value.setter
        def value(self, v):
            self._value = v
            self._store[self.key] = v

    class FakeDB:
        def __init__(self):
            self.store = {_APPLIED: old_env_hash}

        def execute(self, q):
            return SimpleNamespace(scalar_one_or_none=lambda: user)

        def get(self, model, key):
            if key == _APPLIED:
                return WriteThroughRow(self.store, key, self.store[key])

        def add(self, obj):
            self.store[obj.key] = obj.value

        def commit(self):
            pass

    db = FakeDB()

    import app.core.config as cfg
    original = cfg.settings.ADMIN_PASSWORD_HASH
    try:
        cfg.settings.ADMIN_PASSWORD_HASH = new_env_hash
        bs.ensure_admin_bootstrap(db)
        # Password and sessions stay untouched.
        assert user.password_hash == old_env_hash
        assert user.token_version == 0
        assert db.store[_APPLIED] == old_env_hash
    finally:
        cfg.settings.ADMIN_PASSWORD_HASH = original


def test_bootstrap_binds_env_hash_once_when_account_never_had_one(monkeypatch):
    """First apply: an account bound to no bootstrap hash adopts the env one."""
    import app.services.bootstrap as bs
    from datetime import datetime

    import bcrypt

    env_hash = bcrypt.hashpw(b"synthetic-first-apply", bcrypt.gensalt(rounds=4)).decode()
    user = SimpleNamespace(
        username="admin",
        password_hash=bcrypt.hashpw(b"panel-side", bcrypt.gensalt(rounds=4)).decode(),
        is_active=True,
        failed_login_attempts=3,
        locked_until=datetime.utcnow(),
        token_version=0,
        updated_at=datetime.utcnow(),
    )
    store = {}
    db = SimpleNamespace(
        execute=lambda q: SimpleNamespace(scalar_one_or_none=lambda: user),
        get=lambda model, key: None,
        add=lambda obj: store.__setitem__(obj.key, obj.value),
        commit=lambda: None,
    )

    import app.core.config as cfg
    monkeypatch.setattr(cfg.settings, "ADMIN_PASSWORD_HASH", env_hash)
    bs.ensure_admin_bootstrap(db)

    assert user.password_hash == env_hash
    assert user.failed_login_attempts == 0
    assert user.locked_until is None
    assert store[bs._APPLIED_HASH_KEY] == env_hash


# ─── Subsonic dedicated-token-only auth ──────────────────────────────────────

def test_subsonic_rejects_panel_password(monkeypatch):
    """KEY FIX: panel bcrypt password must NOT authenticate via p= anymore."""
    import app.services.subsonic_auth as sa
    from app.core.security import hash_password

    panel_password = "CorrectHorse1!"
    user = SimpleNamespace(
        username="alice",
        password_hash=hash_password(panel_password),
        subsonic_token_enc=None,  # no subsonic token minted yet
        is_active=True,
        locked_until=None,
        failed_login_attempts=0,
    )

    class FakeDB:
        def scalar(self, q):
            return user

        def commit(self):
            pass

    result = sa.authenticate_subsonic(
        FakeDB(), "alice", panel_password, None, None
    )
    assert result is None  # panel password rejected


def test_subsonic_accepts_dedicated_token(monkeypatch):
    import app.services.subsonic_auth as sa
    from app.core.security import hash_password
    from cryptography.fernet import Fernet
    import base64, hashlib

    token = "subsonic-token-abc"
    secret = "test-session-secret"
    key = hashlib.sha256(secret.encode()).digest()
    f = Fernet(base64.urlsafe_b64encode(key))
    enc = f.encrypt(token.encode()).decode()

    user = SimpleNamespace(
        username="alice",
        password_hash=hash_password("whatever-panel-pw"),
        subsonic_token_enc=enc,
        is_active=True,
        locked_until=None,
        failed_login_attempts=0,
    )

    class FakeDB:
        def scalar(self, q):
            return user

        def commit(self):
            pass

    monkeypatch.setattr(sa.settings, "SESSION_SECRET", secret)

    # p=<token> works
    assert sa.authenticate_subsonic(FakeDB(), "alice", token, None, None) is user

    # t=md5(token+salt) works
    import hashlib as _h
    salt = "saltsalt"
    digest = _h.md5((token + salt).encode()).hexdigest()
    assert sa.authenticate_subsonic(FakeDB(), "alice", None, digest, salt) is user

    # Wrong token rejected
    assert sa.authenticate_subsonic(FakeDB(), "alice", "wrong", None, None) is None


# ─── Client-logs rate limit ──────────────────────────────────────────────────

def test_client_logs_rate_limited(monkeypatch):
    import app.api.logs_router as lr

    class FakeLimiter:
        def __init__(self):
            self.calls = []

        def is_rate_limited(self, key, max_requests, window):
            self.calls.append((key, max_requests, window))
            return True

    fake = FakeLimiter()
    monkeypatch.setattr(lr, "limiter", fake)
    monkeypatch.setattr(lr, "get_client_ip", lambda r: "203.0.113.5")

    import asyncio

    import pytest

    with pytest.raises(HTTPException) as exc:
        asyncio.run(lr.ingest_client_logs(
            batch=lr.ClientLogBatch(events=[lr.ClientEvent(msg="hi")]),
            request=SimpleNamespace(),
        ))
    assert exc.value.status_code == 429
    assert fake.calls[0][0] == "clientlogs:203.0.113.5"
    assert fake.calls[0][1] == lr.CLIENT_LOGS_PER_MIN
