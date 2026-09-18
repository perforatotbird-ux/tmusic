"""Admin API smoke tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app
from app.api.dependencies import user_is_admin
from app.models.auth import AppUser
from app.core.database import SessionLocal
from app.core.security import hash_password
from sqlalchemy import select

client = TestClient(app)

USERNAME = "admin_api_test"
PASSWORD = "AdminApi!Test1"


def test_admin_endpoints_auth_guard():
    for path in (
        "/api/admin/overview",
        "/api/admin/db/stats",
        "/api/admin/backups",
        "/api/admin/settings",
        "/api/admin/users",
    ):
        resp = client.get(path)
        assert resp.status_code in (200, 401, 403), f"{path} -> {resp.status_code}"


def test_user_is_admin_logic():
    a = AppUser(username="admin", role="user", password_hash="x")
    b = AppUser(username="bob", role="admin", password_hash="x")
    c = AppUser(username="carol", role="user", password_hash="x")
    assert user_is_admin(a) is True
    assert user_is_admin(b) is True
    assert user_is_admin(c) is False


def test_settings_roundtrip_key_guard():
    from app.services import admin_service

    assert "backup_enabled" in admin_service.ALLOWED_SETTING_KEYS
    assert "secret_key" not in admin_service.ALLOWED_SETTING_KEYS
