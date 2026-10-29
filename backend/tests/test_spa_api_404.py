"""Unknown API paths must not fall through to the SPA shell (API-001)."""
from fastapi.testclient import TestClient

from app import main
from app.main import app


client = TestClient(app)


def test_unknown_api_path_returns_json_404():
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_unknown_health_path_is_not_spa_shell():
    response = client.get("/health/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_readiness_reports_ready_when_database_answers(monkeypatch):
    monkeypatch.setattr(main, "_database_ready", lambda: True)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_fails_when_database_is_down(monkeypatch):
    monkeypatch.setattr(main, "_database_ready", lambda: False)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["database"] is False
