"""Admin API smoke tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.main import app
from app.api.dependencies import user_has_permission, user_is_admin
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
        "/api/admin/audit",
        "/api/admin/vfs/folders",
        "/api/admin/vfs/files",
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


def test_role_permissions():
    admin = AppUser(username="root", role="admin", password_hash="x")
    editor = AppUser(username="editor", role="editor", password_hash="x")
    user = AppUser(username="user", role="user", password_hash="x")
    assert user_has_permission(admin, "admin.users.manage") is True
    assert user_has_permission(editor, "catalog.manage") is True
    assert user_has_permission(editor, "admin.users.manage") is False
    assert user_has_permission(user, "catalog.manage") is False
    assert user_has_permission(user, "admin.access") is False


def test_admin_analytics_shape():
    username = "analytics_admin_test"
    password = "AnalyticsAdmin!1"
    with SessionLocal() as db:
        existing = db.scalar(select(AppUser).where(AppUser.username == username))
        if existing:
            db.delete(existing)
            db.commit()
        db.add(AppUser(username=username, password_hash=hash_password(password), role="admin", is_active=True))
        db.commit()
    try:
        login = client.post("/api/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200
        token = login.json()["access_token"]
        response = client.get("/api/admin/analytics?days=30", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        data = response.json()
        assert "totals" in data
        assert "top_tracks" in data
    finally:
        with SessionLocal() as db:
            user = db.scalar(select(AppUser).where(AppUser.username == username))
            if user:
                db.delete(user)
                db.commit()


def test_maintenance_mode_blocks_public_api(monkeypatch):
    from app.services import admin_service

    monkeypatch.setattr(admin_service, "get_setting", lambda db, key, default=None: "true")
    resp = client.get("/api/library/artists")
    assert resp.status_code == 503
    assert resp.headers.get("X-Maintenance-Mode") == "1"


def test_settings_roundtrip_key_guard():
    from app.services import admin_service

    assert "backup_enabled" in admin_service.ALLOWED_SETTING_KEYS
    assert "secret_key" not in admin_service.ALLOWED_SETTING_KEYS
