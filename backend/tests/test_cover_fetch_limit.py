"""Cover downloads must be bounded while streaming (SEC-009)."""
import asyncio

from app.services import cover_cache_service


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks
        self.iterated = 0

    async def iter_chunked(self, size):
        for chunk in self._chunks:
            self.iterated += 1
            yield chunk


class _FakeResponse:
    def __init__(self, chunks, content_length=None):
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.content = _FakeContent(chunks)


def test_read_limited_body_accepts_payload_within_cap(monkeypatch):
    monkeypatch.setattr(cover_cache_service, "MAX_IMAGE_BYTES", 8)
    response = _FakeResponse([b"1234", b"5678"])
    assert asyncio.run(cover_cache_service._read_limited_body(response)) == b"12345678"


def test_read_limited_body_stops_on_oversized_stream(monkeypatch):
    monkeypatch.setattr(cover_cache_service, "MAX_IMAGE_BYTES", 8)
    response = _FakeResponse([b"1234", b"5678", b"9"])
    assert asyncio.run(cover_cache_service._read_limited_body(response)) is None


def test_read_limited_body_rejects_declared_oversized_payload(monkeypatch):
    monkeypatch.setattr(cover_cache_service, "MAX_IMAGE_BYTES", 8)
    response = _FakeResponse([b"1234"], content_length=9)
    assert asyncio.run(cover_cache_service._read_limited_body(response)) is None
    assert response.content.iterated == 0
