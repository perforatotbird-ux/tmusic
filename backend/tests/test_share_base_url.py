"""Share links must not follow a forged Host header when PUBLIC_BASE_URL is set (SEC-012)."""
from starlette.requests import Request

from app.api import share_router
from app.core.config import settings


def _request(host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": (host, 443),
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", host.encode())],
        }
    )


def test_share_url_prefers_configured_public_base(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://music.example.com/")
    assert share_router._share_url(_request("evil.example"), "tok") == (
        "https://music.example.com/s/tok"
    )


def test_share_url_falls_back_to_request_base(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", None)
    url = share_router._share_url(_request("music.lan:8005"), "tok")
    assert url == "https://music.lan:8005/s/tok"
