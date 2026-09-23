"""
T-Music Subsonic / OpenSubsonic response builders.
"""
from datetime import datetime
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape, quoteattr

SERVER_NAME = "T-Music"
SERVER_VERSION = "1.0.0"
API_VERSION = "1.16.1"
OPEN_SUBSONIC = True

# List key -> child tag in XML
_LIST_TAGS = {
    "artists": "artist",
    "similarArtists": "artist",
    "albumList2": "album",
    "albumList": "album",
    "musicFolders": "musicFolder",
    "playlists": "playlist",
    "randomSongs": "child",
    "songs": "child",
    "children": "child",
    "shortcuts": "shortcut",
    "indexes": "index",
    "similarSongs": "child",
    "topSongs": "child",
    "nowPlayingList": "child",
    "starred": "child",
    "starred2": "child",
    "structuredLyrics": "structuredLyric",
    "line": "line",
    "genres": "genre",
    "share": "entry",
    "shares": "entry",
}


def ok_envelope(inner_key: Optional[str] = None, inner: Any = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "status": "ok",
        "version": API_VERSION,
        "type": "tmusic",
        "serverVersion": SERVER_VERSION,
        "openSubsonic": OPEN_SUBSONIC,
    }
    if inner_key:
        body[inner_key] = inner
    return {"subsonic-response": body}


def fail(code: int, message: str) -> Dict[str, Any]:
    return {
        "subsonic-response": {
            "status": "failed",
            "version": API_VERSION,
            "type": "tmusic",
            "serverVersion": SERVER_VERSION,
            "error": {"code": code, "message": message},
        }
    }


def _x(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return escape(str(value))


def _tag(name: str, value: Any) -> str:
    return f"<{name}>{_x(value)}</{name}>"


def dict_to_subsonic_xml(payload: Dict[str, Any]) -> str:
    root = payload.get("subsonic-response") or {}
    reserved = ("status", "version", "type", "serverVersion", "openSubsonic")
    attrs = "".join(
        f" {k}={quoteattr(_x(root[k]))}" for k in reserved if k in root
    )
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', f"<subsonic-response{attrs}>"]
    for key, value in root.items():
        if key in reserved:
            continue
        parts.append(value_to_xml(key, value))
    parts.append("</subsonic-response>")
    return "".join(parts)


def value_to_xml(name: str, value: Any) -> str:
    if isinstance(value, list):
        tag = _LIST_TAGS.get(name)
        if tag is None:
            if name in ("artist", "album", "child", "playlist", "match", "song"):
                tag = name
            elif name.endswith("s"):
                tag = name[:-1]
            else:
                tag = "child"
        return "".join(value_to_xml(tag, item) for item in value)

    if isinstance(value, dict):
        if name == "error":
            attr_s = "".join(f" {k}={quoteattr(_x(v))}" for k, v in value.items())
            return f"<error{attr_s} />"

        if name in ("searchResult3", "searchResult2"):
            # Child collections + optional id attr
            scalar = {k: v for k, v in value.items() if not isinstance(v, (list, dict))}
            lists = {k: v for k, v in value.items() if isinstance(v, list)}
            attr_s = "".join(f" {k}={quoteattr(_x(v))}" for k, v in scalar.items())
            inner = "".join(value_to_xml(k, v) for k, v in lists.items())
            return f"<{name}{attr_s}>{inner}</{name}>" if lists else f"<{name}{attr_s} />"

        scalars = {k: v for k, v in value.items() if not isinstance(v, (dict, list))}
        children = {k: v for k, v in value.items() if isinstance(v, (dict, list))}
        attr_s = "".join(f" {k}={quoteattr(_x(v))}" for k, v in scalars.items())
        if not children:
            return f"<{name}{attr_s} />"
        inner = "".join(value_to_xml(k, v) for k, v in children.items())
        return f"<{name}{attr_s}>{inner}</{name}>"

    # Subsonic scalars are usually attributes on the parent; bare elements ok too
    return f"<{name}>{_x(value)}</{name}>"


def ts(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _title_from_name(name: Optional[str]) -> str:
    base = name or "Track"
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base


def song_entry(
    vf,
    artist: str = "Unknown",
    album: str = "Unknown",
    artist_id: Optional[int] = None,
    album_id: Optional[int] = None,
    track_no: int = 1,
) -> Dict[str, Any]:
    display = _title_from_name(vf.name)
    title = display
    parsed_artist = None
    if " - " in display:
        left, right = display.split(" - ", 1)
        if left and right:
            parsed_artist = left.strip()
            title = right.strip()
    if artist == "Unknown" and parsed_artist:
        artist = parsed_artist

    suffix = "mp3"
    if "." in (vf.name or ""):
        ext = vf.name.rsplit(".", 1)[-1].lower()
        if ext in ("mp3", "flac", "ogg", "m4a", "aac", "wav", "opus", "wma"):
            suffix = ext

    entry: Dict[str, Any] = {
        "id": f"fl-{vf.id}",
        "isDir": False,
        "title": title,
        "album": album,
        "artist": artist,
        "track": track_no,
        "coverArt": f"al-{album_id}" if album_id else f"fl-{vf.id}",
        "size": vf.size or 0,
        "contentType": vf.mime_type or "audio/mpeg",
        "suffix": suffix,
        "duration": 0,
        "bitRate": 0,
        "path": f"{artist}/{album}/{display}.{suffix}",
        "isVideo": False,
        "type": "music",
        "created": ts(vf.created_at),
    }
    if album_id:
        entry["albumId"] = f"al-{album_id}"
        entry["parent"] = f"al-{album_id}"
    if artist_id:
        entry["artistId"] = f"ar-{artist_id}"
    return entry


def artist_entry(a, album_count: int = 0) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": f"ar-{a.id}",
        "name": a.display_name,
        "albumCount": album_count,
        "coverArt": f"ar-{a.id}",
    }
    return out


def album_entry(
    alb,
    artist: str = "Unknown",
    artist_id: Optional[int] = None,
    song_count: int = 0,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": f"al-{alb.id}",
        "name": alb.title,
        "artist": artist,
        "coverArt": f"al-{alb.id}",
        "songCount": song_count,
        "duration": 0,
        "created": ts(alb.created_at),
        "type": "album",
    }
    if alb.year:
        out["year"] = alb.year
    if artist_id:
        out["artistId"] = f"ar-{artist_id}"
    return out


def playlist_entry(pl, song_count: int = 0) -> Dict[str, Any]:
    return {
        "id": f"pl-{pl.id}",
        "name": pl.name,
        "owner": "admin",
        "public": False,
        "songCount": song_count,
        "duration": 0,
        "created": ts(pl.created_at),
        "changed": ts(pl.updated_at),
    }
