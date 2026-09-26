"""
External provider disk cache: TTL, negative caching, budgets, namespaces,
and integration with the lyrics/lastfm services (all network stubbed).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.disk_cache import NEGATIVE_TTL_SEC, ExternalDiskCache
from app.services import lyrics_service

PARTS = {"artist": "queen", "track": "bohemian"}


def _cache(tmp_path):
    return ExternalDiskCache(tmp_path / "external")


def test_put_get_roundtrip(tmp_path):
    c = _cache(tmp_path)
    assert c.get("lyrics", PARTS) == (False, None)
    c.put("lyrics", PARTS, {"found": True, "plain": "x"}, ttl_sec=3600)
    hit, value = c.get("lyrics", PARTS)
    assert hit is True
    assert value["plain"] == "x"


def test_key_ordering_canonical(tmp_path):
    c = _cache(tmp_path)
    c.put("lyrics", {"a": 1, "b": 2}, {"v": "first"}, ttl_sec=3600)
    hit, value = c.get("lyrics", {"b": 2, "a": 1})
    assert hit is True
    assert value["v"] == "first"


def test_ttl_expiry(tmp_path):
    c = _cache(tmp_path)
    c.put("lastfm", PARTS, {"v": 1}, ttl_sec=3600)
    # Rewind the stored expiry by hand to avoid sleeping.
    directory = c._ns_dir("lastfm")
    path = next(directory.glob("*.json"))
    import json

    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["expires_at"] = time.time() - 1
    path.write_text(json.dumps(entry), encoding="utf-8")
    assert c.get("lastfm", PARTS) == (False, None)


def test_negative_entry_uses_short_ttl_and_reads_as_none(tmp_path):
    c = _cache(tmp_path)
    c.put("lyrics", PARTS, None, ttl_sec=90 * 24 * 3600)
    hit, value = c.get("lyrics", PARTS)
    assert hit is True
    assert value is None  # cached miss
    directory = c._ns_dir("lyrics")
    path = next(directory.glob("*.json"))
    import json

    entry = json.loads(path.read_text(encoding="utf-8"))
    ttl = entry["expires_at"] - time.time()
    assert ttl <= NEGATIVE_TTL_SEC + 5


def test_namespaces_are_isolated(tmp_path):
    c = _cache(tmp_path)
    c.put("lyrics", PARTS, {"v": "l"}, ttl_sec=3600)
    c.put("lastfm", PARTS, {"v": "m"}, ttl_sec=3600)
    assert c.get("lyrics", PARTS)[1] == {"v": "l"}
    assert c.get("lastfm", PARTS)[1] == {"v": "m"}
    assert c.clear("lyrics") == 1
    assert c.get("lyrics", PARTS)[0] is False
    assert c.get("lastfm", PARTS)[0] is True


def test_item_budget_evicts_oldest_accessed(tmp_path):
    c = _cache(tmp_path)
    import app.core.disk_cache as dc

    original = dc._NS_BUDGET["lyrics"]
    dc._NS_BUDGET["lyrics"] = {"max_items": 2, "max_bytes": 10 ** 9}
    try:
        for i in range(5):
            c.put("lyrics", {"n": i}, {"v": i}, ttl_sec=3600)
            time.sleep(0.01)
        files = list(c._ns_dir("lyrics").glob("*.json"))
        assert len(files) == 2
        # Survivors are the two most recently written.
        import json

        survivors = sorted(
            json.loads(p.read_text(encoding="utf-8"))["value"]["v"] for p in files
        )
        assert survivors == [3, 4]
    finally:
        dc._NS_BUDGET["lyrics"] = original


def test_corrupt_entry_reads_as_miss(tmp_path):
    c = _cache(tmp_path)
    c.put("lyrics", PARTS, {"v": 1}, ttl_sec=3600)
    path = next(c._ns_dir("lyrics").glob("*.json"))
    path.write_text("{not json", encoding="utf-8")
    assert c.get("lyrics", PARTS) == (False, None)


def test_lyrics_caches_positive_and_skips_upstream(tmp_path, monkeypatch):
    monkeypatch.setattr(lyrics_service.external_cache, "root", tmp_path / "external")
    calls = []

    def fake_lookup(**kwargs):
        calls.append(kwargs)
        return {"found": True, "plain": "Is this the real life?"}

    monkeypatch.setattr(lyrics_service, "_lookup_upstream", fake_lookup)
    first = lyrics_service.get_lyrics(artist="Queen", track="Bohemian Rhapsody")
    second = lyrics_service.get_lyrics(artist="Queen", track="Bohemian Rhapsody")
    assert first == second
    assert len(calls) == 1, "second identical call must be served from disk cache"


def test_lyrics_negative_cache_blocks_refetch(tmp_path, monkeypatch):
    monkeypatch.setattr(lyrics_service.external_cache, "root", tmp_path / "external")
    calls = []

    def fake_lookup(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(lyrics_service, "_lookup_upstream", fake_lookup)
    assert lyrics_service.get_lyrics(artist="Nobody", track="Nope") is None
    assert lyrics_service.get_lyrics(artist="Nobody", track="Nope") is None
    assert len(calls) == 1, "misses must be cached with short TTL"


def test_lastfm_caches_positive_and_negative(tmp_path, monkeypatch):
    from app.services import lastfm_service

    monkeypatch.setattr(lastfm_service.external_cache, "root", tmp_path / "external")
    monkeypatch.setattr(lastfm_service, "metadata_ready", lambda: True)
    calls = []

    def fake_fetch(artist):
        calls.append(artist)
        return {"name": artist, "similar": ["aaa", "bbb"]} if artist == "Queen" else None

    monkeypatch.setattr(lastfm_service, "_fetch_artist_info", fake_fetch)
    ok = lastfm_service.get_artist_info("Queen")
    ok2 = lastfm_service.get_artist_info("queen")  # casefold: same identity
    assert ok == ok2
    miss = lastfm_service.get_artist_info("Zzz")
    miss2 = lastfm_service.get_artist_info("Zzz")
    assert miss is None and miss2 is None
    assert calls == ["Queen", "Zzz"]  # each unique identity fetched exactly once
