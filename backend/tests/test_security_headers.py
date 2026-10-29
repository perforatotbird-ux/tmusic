"""CSP must not allow third-party origins or broad inline styles (SEC-011)."""
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_csp_blocks_third_party_origins_and_style_blocks():
    response = client.get("/health")
    csp = response.headers["content-security-policy"]

    assert "connect-src 'self';" in csp
    assert "img-src 'self' data: blob:;" in csp
    assert "style-src 'self';" in csp
    assert "style-src 'unsafe-inline'" not in csp
    assert "connect-src 'self' https:" not in csp
    assert "img-src 'self' data: https:" not in csp
    assert "object-src 'none';" in csp
