"""
T-Music Subsonic / OpenSubsonic REST API.

Mount: /rest/*
Clients: Ultrasonic, Symfonium, DSub, Subtracks, etc.
Auth query params: u, p (password/token) OR t=md5(token+salt)&s=salt, c, v, f=json|xml
"""
import logging
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.auth import AppUser
from app.models.music import MusicAlbum, MusicArtist
from app.models.playlist import Playlist, PlaylistItem
from app.models.share import ShareLink
from app.models.vfs import VFile
from app.repositories.file_repository import FileRepository
from app.services import lastfm_service, lyrics_service, subsonic_auth
from app.services.playback_service import playback_service
from app.services.stream_service import stream_service
from app.services.subsonic_response import (
    album_entry,
    artist_entry,
    dict_to_subsonic_xml,
    fail,
    ok_envelope,
    playlist_entry,
    song_entry,
    ts,
)
from app.utils.like import escape_like

logger = logging.getLogger("tmusic.subsonic")
router = APIRouter(tags=["subsonic"])

_ID_RE = re.compile(r"^(ar|al|fl|pl)-(\d+)$")


def _parse_id(raw: Any) -> Tuple[Optional[str], Optional[int]]:
    if raw is None:
        return None, None
    s = str(raw).strip()
    m = _ID_RE.match(s)
    if m:
        return m.group(1), int(m.group(2))
    if s.isdigit():
        return None, int(s)
    return None, None


def _wants_xml(f: Optional[str]) -> bool:
    return (f or "xml").lower() != "json"


def _http_auth_user(
    request: Request,
    db: Session,
    u: Optional[str],
    p: Optional[str],
    t: Optional[str],
    s: Optional[str],
) -> Optional[AppUser]:
    return subsonic_auth.authenticate_subsonic(
        db,
        username=u,
        password=p,
        token=t,
        salt=s,
    )


def _respond(payload: Dict[str, Any], f: Optional[str], status_code: int = 200):
    if _wants_xml(f):
        return Response(
            content=dict_to_subsonic_xml(payload),
            media_type="text/xml; charset=utf-8",
            status_code=status_code,
        )
    return JSONResponse(content=payload, status_code=status_code)


def _catalog_artist(db: Session, artist_id: int) -> Optional[MusicArtist]:
    return db.scalar(select(MusicArtist).where(MusicArtist.id == artist_id))


def _album_songs(db: Session, album: MusicAlbum) -> list:
    artist = _catalog_artist(db, album.artist_id)
    tracks = FileRepository.list_tracks_recursive(db, album.folder_id)
    out = []
    for i, vf in enumerate(tracks, start=1):
        out.append(
            song_entry(
                vf,
                artist=artist.display_name if artist else "Unknown",
                album=album.title,
                artist_id=album.artist_id,
                album_id=album.id,
                track_no=i,
            )
        )
    return out


def _artist_songs(db: Session, artist: MusicArtist, limit: int = 500) -> list:
    albums = db.scalars(
        select(MusicAlbum).where(MusicAlbum.artist_id == artist.id).order_by(MusicAlbum.year, MusicAlbum.title)
    ).all()
    out = []
    for alb in albums:
        out.extend(_album_songs(db, alb))
        if len(out) >= limit:
            break
    return out[:limit]


def _random_song_rows(db: Session, size: int) -> list:
    stmt = (
        select(VFile)
        .where(VFile.is_trash == False, VFile.mime_type.like("audio/%"))  # noqa: E712
        .order_by(func.random())
        .limit(max(1, min(size, 500)))
    )
    files = list(db.scalars(stmt).all())
    rows = []
    for vf in files:
        alb = None
        artist = None
        if vf.folder_id:
            alb = db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))
            if alb:
                artist = _catalog_artist(db, alb.artist_id)
        rows.append(
            song_entry(
                vf,
                artist=artist.display_name if artist else "Unknown",
                album=alb.title if alb else "Unknown",
                artist_id=alb.artist_id if alb else None,
                album_id=alb.id if alb else None,
            )
        )
    return rows


def _file_song(db: Session, vf: VFile) -> Dict[str, Any]:
    alb = None
    artist = None
    if vf.folder_id:
        alb = db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))
        if alb:
            artist = _catalog_artist(db, alb.artist_id)
    return song_entry(
        vf,
        artist=artist.display_name if artist else "Unknown",
        album=alb.title if alb else "Unknown",
        artist_id=alb.artist_id if alb else None,
        album_id=alb.id if alb else None,
    )


def _file_artist_name(db: Session, vf: VFile) -> Optional[str]:
    if not vf.folder_id:
        return None
    alb = db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))
    if not alb:
        return None
    artist = _catalog_artist(db, alb.artist_id)
    return artist.display_name if artist else None


def _multi_ids(request: Request, params: Dict[str, Any], key: str = "id") -> list:
    vals = request.query_params.getlist(key) or request.query_params.getlist(f"{key}[]")
    if not vals and params.get(key):
        vals = [params.get(key)]
    return vals


def _get_song(db: Session, raw_id: Any):
    kind, num = _parse_id(raw_id)
    if num is None:
        return None
    vf = FileRepository.get_by_id(db, num)
    if not vf or vf.is_trash or not (vf.mime_type or "").startswith("audio/"):
        return None
    return vf


_LRC_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")


def _parse_lrc(synced: str) -> list:
    out = []
    for raw in synced.splitlines():
        m = _LRC_RE.search(raw)
        if not m:
            continue
        mm, ss = int(m.group(1)), int(m.group(2))
        frac = m.group(3) or "0"
        ms = (mm * 60 + ss) * 1000 + int(frac.ljust(3, "0")[:3])
        value = _LRC_RE.sub("", raw).strip()
        if value:
            out.append({"value": value, "start": ms})
    return out


async def _handle(
    method: str,
    request: Request,
    db: Session,
    params: Dict[str, Any],
    f: Optional[str],
):
    method = (method or "").removesuffix(".view").strip()
    user = _http_auth_user(
        request,
        db,
        params.get("u"),
        params.get("p"),
        params.get("t"),
        params.get("s"),
    )
    # public-ish methods still require auth in Subsonic; we enforce always
    if not user:
        return _respond(fail(40, "Wrong username or password."), f, status_code=200)

    def ok(key=None, inner=None):
        return _respond(ok_envelope(key, inner), f)

    if method == "ping":
        return ok()

    if method == "getLicense":
        return ok("license", {"valid": True, "email": "tmusic@localhost", "licenseExpires": "2099-12-31T00:00:00"})

    if method == "getUser":
        return ok(
            "user",
            {
                "username": user.username,
                "email": user.username,
                "scrobblingEnabled": bool(settings.lastfm_scrobble_ready),
                "adminRole": True,
                "settingsRole": True,
                "downloadRole": True,
                "uploadRole": False,
                "playlistRole": True,
                "coverArtRole": True,
                "commentRole": False,
                "podcastRole": False,
                "streamRole": True,
                "jukeboxRole": False,
                "shareRole": True,
                "videoConversionRole": False,
                "folder": [1],
            },
        )

    if method in ("getMusicFolders",):
        return ok("musicFolders", [{"id": 1, "name": "T-Music"}])

    if method in ("getIndexes", "getArtists"):
        stmt = select(MusicArtist).order_by(MusicArtist.display_name)
        artists = list(db.scalars(stmt).all())
        entries = []
        index_map: Dict[str, list] = {}
        for a in artists:
            cnt = db.scalar(
                select(func.count(MusicAlbum.id)).where(MusicAlbum.artist_id == a.id)
            )
            entry = artist_entry(a, album_count=cnt or 0)
            entries.append(entry)
            letter = (a.display_name or "#")[0].upper()
            if not letter.isalpha():
                letter = "#"
            index_map.setdefault(letter, []).append(entry)
        indexes = [
            {"name": letter, "artist": index_map[letter]}
            for letter in sorted(index_map.keys())
        ]
        if method == "getIndexes":
            return ok(
                "indexes",
                {
                    "lastModified": 0,
                    "ignoredArticles": "The An A Die Das Ein Eine Les Le La",
                    "shortcut": [],
                    "index": indexes,
                },
            )
        # getArtists OpenSubsonic: { index: [{name, artist:[]}] }
        return ok("artists", {"index": indexes})

    if method == "getArtist":
        kind, num = _parse_id(params.get("id"))
        if kind not in (None, "ar") or num is None:
            return _respond(fail(70, "Artist not found."), f)
        artist = _catalog_artist(db, num)
        if not artist:
            return _respond(fail(70, "Artist not found."), f)
        albums = db.scalars(
            select(MusicAlbum).where(MusicAlbum.artist_id == artist.id).order_by(MusicAlbum.year, MusicAlbum.title)
        ).all()
        album_list = []
        for alb in albums:
            sc = db.scalar(
                select(func.count(VFile.id)).where(
                    VFile.folder_id == alb.folder_id,
                    VFile.is_trash == False,  # noqa: E712
                    VFile.mime_type.like("audio/%"),
                )
            )
            album_list.append(album_entry(alb, artist=artist.display_name, artist_id=artist.id, song_count=sc or 0))
        payload = artist_entry(artist, album_count=len(album_list))
        payload["album"] = album_list
        return ok("artist", payload)

    if method in ("getAlbum", "getAlbumInfo2"):
        kind, num = _parse_id(params.get("id"))
        if num is None:
            return _respond(fail(70, "Album not found."), f)
        # bare numeric id may mean album
        alb = db.scalar(select(MusicAlbum).where(MusicAlbum.id == num))
        if not alb:
            return _respond(fail(70, "Album not found."), f)
        artist = _catalog_artist(db, alb.artist_id)
        songs = _album_songs(db, alb)
        payload = album_entry(
            alb,
            artist=artist.display_name if artist else "Unknown",
            artist_id=alb.artist_id,
            song_count=len(songs),
        )
        payload["song"] = songs
        return ok("album", payload)

    if method in ("getSong",):
        kind, num = _parse_id(params.get("id"))
        if num is None:
            return _respond(fail(70, "Song not found."), f)
        vf = FileRepository.get_by_id(db, num)
        if not vf:
            return _respond(fail(70, "Song not found."), f)
        alb = None
        artist = None
        if vf.folder_id:
            alb = db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))
            if alb:
                artist = _catalog_artist(db, alb.artist_id)
        entry = song_entry(
            vf,
            artist=artist.display_name if artist else "Unknown",
            album=alb.title if alb else "Unknown",
            artist_id=alb.artist_id if alb else None,
            album_id=alb.id if alb else None,
        )
        return ok("song", entry)

    if method in ("getAlbumList", "getAlbumList2"):
        try:
            size = int(params.get("size") or 50)
        except (TypeError, ValueError):
            size = 50
        size = max(1, min(size, 500))
        type_ = (params.get("type") or "alphabeticalByArtist").lower()
        stmt = select(MusicAlbum)
        if type_ in ("recent", "newest"):
            stmt = stmt.order_by(MusicAlbum.created_at.desc())
        elif type_ in ("random",):
            stmt = stmt.order_by(func.random())
        elif type_ in ("frequent", "recentlyplayed"):
            stmt = stmt.order_by(MusicAlbum.updated_at.desc())
        else:
            stmt = stmt.order_by(MusicAlbum.title)
        stmt = stmt.limit(size)
        albums = list(db.scalars(stmt).all())
        entries = []
        for alb in albums:
            artist = _catalog_artist(db, alb.artist_id)
            entries.append(
                album_entry(
                    alb,
                    artist=artist.display_name if artist else "Unknown",
                    artist_id=alb.artist_id,
                )
            )
        return ok("albumList2" if method == "getAlbumList2" else "albumList", entries)

    if method in ("getRandomSongs",):
        try:
            size = int(params.get("size") or 10)
        except (TypeError, ValueError):
            size = 10
        return ok("randomSongs", _random_song_rows(db, size))

    if method in ("search2", "search3"):
        query = (params.get("query") or "").strip()
        try:
            song_count = int(params.get("songCount") or 20)
            album_count = int(params.get("albumCount") or 10)
            artist_count = int(params.get("artistCount") or 10)
        except (TypeError, ValueError):
            song_count, album_count, artist_count = 20, 10, 10
        if not query:
            key = "searchResult3" if method == "search3" else "searchResult2"
            return ok(key, {})
        pattern = f"%{escape_like(query)}%"
        artists = list(
            db.scalars(
                select(MusicArtist)
                .where(
                    or_(
                        MusicArtist.display_name.ilike(pattern, escape="\\"),
                        MusicArtist.search_name.ilike(pattern, escape="\\"),
                    )
                )
                .limit(artist_count)
            ).all()
        )
        albums = list(
            db.scalars(
                select(MusicAlbum).where(MusicAlbum.title.ilike(pattern, escape="\\")).limit(album_count)
            ).all()
        )
        tracks = list(
            db.scalars(
                select(VFile)
                .where(
                    VFile.is_trash == False,  # noqa: E712
                    VFile.mime_type.like("audio/%"),
                    VFile.name.ilike(pattern, escape="\\"),
                )
                .limit(song_count)
            ).all()
        )
        result = {
            "artist": [artist_entry(a) for a in artists],
            "album": [
                album_entry(
                    alb,
                    artist=(_catalog_artist(db, alb.artist_id).display_name if _catalog_artist(db, alb.artist_id) else "Unknown"),
                    artist_id=alb.artist_id,
                )
                for alb in albums
            ],
            "song": [],
        }
        for vf in tracks:
            alb = None
            artist = None
            if vf.folder_id:
                alb = db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))
                if alb:
                    artist = _catalog_artist(db, alb.artist_id)
            result["song"].append(
                song_entry(
                    vf,
                    artist=artist.display_name if artist else "Unknown",
                    album=alb.title if alb else "Unknown",
                    artist_id=alb.artist_id if alb else None,
                    album_id=alb.id if alb else None,
                )
            )
        key = "searchResult3" if method == "search3" else "searchResult2"
        return ok(key, result)

    if method in ("getPlaylists",):
        rows = db.scalars(
            select(Playlist).where(Playlist.user_id == user.id).order_by(Playlist.updated_at.desc())
        ).all()
        entries = []
        for pl in rows:
            cnt = db.scalar(select(func.count(PlaylistItem.id)).where(PlaylistItem.playlist_id == pl.id))
            entries.append(playlist_entry(pl, song_count=cnt or 0))
        return ok("playlists", entries)

    if method in ("getPlaylist",):
        kind, num = _parse_id(params.get("id"))
        if num is None:
            return _respond(fail(70, "Playlist not found."), f)
        pl = db.scalar(select(Playlist).where(Playlist.id == num, Playlist.user_id == user.id))
        if not pl:
            return _respond(fail(70, "Playlist not found."), f)
        items = list(
            db.execute(
                select(PlaylistItem, VFile)
                .outerjoin(VFile, VFile.id == PlaylistItem.file_id)
                .where(PlaylistItem.playlist_id == pl.id)
                .order_by(PlaylistItem.position, PlaylistItem.id)
            ).all()
        )
        songs = []
        for idx, (item, vf) in enumerate(items, start=1):
            if not vf:
                continue
            alb = None
            artist = None
            if vf.folder_id:
                alb = db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))
                if alb:
                    artist = _catalog_artist(db, alb.artist_id)
            songs.append(
                song_entry(
                    vf,
                    artist=artist.display_name if artist else "Unknown",
                    album=alb.title if alb else "Unknown",
                    artist_id=alb.artist_id if alb else None,
                    album_id=alb.id if alb else None,
                    track_no=idx,
                )
            )
        payload = playlist_entry(pl, song_count=len(songs))
        payload["entry"] = songs
        return ok("playlist", payload)

    if method in ("createPlaylist",):
        name = params.get("name") or "Playlist"
        playlist_id = params.get("playlistId")
        song_ids = request.query_params.getlist("songId") or request.query_params.getlist("songId[]")
        if playlist_id:
            kind, num = _parse_id(playlist_id)
            pl = db.scalar(select(Playlist).where(Playlist.id == num, Playlist.user_id == user.id)) if num else None
        else:
            new_pl = Playlist(user_id=user.id, name=str(name)[:120])
            db.add(new_pl)
            db.commit()
            db.refresh(new_pl)
            pl = new_pl
        if not pl:
            return _respond(fail(50, "Playlist not found."), f)
        if song_ids:
            db.query(PlaylistItem).filter(PlaylistItem.playlist_id == pl.id).delete()
            pos = 1
            for sid in song_ids:
                _, num = _parse_id(sid)
                if num is None:
                    continue
                if not FileRepository.get_by_id(db, num):
                    continue
                db.add(PlaylistItem(playlist_id=pl.id, file_id=num, position=pos))
                pos += 1
            db.commit()
        payload = playlist_entry(pl, song_count=db.scalar(
            select(func.count(PlaylistItem.id)).where(PlaylistItem.playlist_id == pl.id)
        ) or 0)
        return ok("playlist", payload)

    if method in ("updatePlaylist",):
        kind, num = _parse_id(params.get("playlistId"))
        pl = db.scalar(select(Playlist).where(Playlist.id == num, Playlist.user_id == user.id)) if num else None
        if not pl:
            return _respond(fail(70, "Playlist not found."), f)
        name = params.get("name")
        if name:
            pl.name = str(name)[:120]
        song_ids = request.query_params.getlist("songId") or request.query_params.getlist("songId[]")
        if song_ids:
            db.query(PlaylistItem).filter(PlaylistItem.playlist_id == pl.id).delete()
            pos = 1
            for sid in song_ids:
                _, fid = _parse_id(sid)
                if fid and FileRepository.get_by_id(db, fid):
                    db.add(PlaylistItem(playlist_id=pl.id, file_id=fid, position=pos))
                    pos += 1
        db.commit()
        return ok("playlist", playlist_entry(pl))

    if method in ("deletePlaylist",):
        kind, num = _parse_id(params.get("id"))
        pl = db.scalar(select(Playlist).where(Playlist.id == num, Playlist.user_id == user.id)) if num else None
        if not pl:
            return _respond(fail(70, "Playlist not found."), f)
        db.delete(pl)
        db.commit()
        return ok()

    if method in ("stream", "download"):
        kind, num = _parse_id(params.get("id"))
        if num is None:
            return _respond(fail(70, "Media not found."), f)
        track = stream_service.get_track_or_404(db, num)
        total_size = stream_service.get_track_size(track)
        if total_size <= 0:
            return _respond(fail(0, "Invalid media size."), f)
        range_header = request.headers.get("Range")
        start, end, content_length = stream_service.parse_range_header(range_header, total_size)
        media_type = track.mime_type or "audio/mpeg"
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{total_size}",
            "Content-Length": str(content_length),
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="{track.id}.bin"',
        }
        gen = stream_service.get_stream_generator(track, start, content_length)
        return StreamingResponse(
            gen,
            status_code=206 if range_header else 200,
            media_type=media_type,
            headers=headers,
        )

    if method in ("getCoverArt",):
        kind, num = _parse_id(params.get("id"))
        # Prefer album cover via existing proxy when possible; empty 1x1 otherwise
        if kind == "al" and num:
            alb = db.scalar(select(MusicAlbum).where(MusicAlbum.id == num))
            if alb and alb.custom_image_key:
                from fastapi.responses import RedirectResponse

                return RedirectResponse(url=f"/api/library/cover?key={alb.custom_image_key}&custom=1")
            if alb and alb.cover_url:
                from fastapi.responses import RedirectResponse
                from urllib.parse import quote

                return RedirectResponse(url=f"/api/library/cover?url={quote(alb.cover_url, safe='')}")
        # 1x1 transparent PNG
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
        )
        return Response(content=png, media_type="image/png")

    if method in ("getScanStatus", "startScan"):
        return ok("scanStatus", {"scanning": False, "count": 0})

    if method in ("star", "unstar"):
        want = method == "star"
        ids = _multi_ids(request, params, "id")
        for sid in ids:
            vf = _get_song(db, sid)
            if vf:
                playback_service.set_favorite(db, user.id, vf.id, want)
        return ok()

    if method in ("getStarred", "getStarred2"):
        rows, _total = playback_service.list_favorites(db, user.id, limit=500)
        songs = []
        for _fav, vf in rows:
            if vf:
                songs.append(_file_song(db, vf))
        return ok("starred2" if method == "getStarred2" else "starred", {"song": songs})

    if method in ("addSongRating",):
        vf = _get_song(db, params.get("id"))
        if not vf:
            return _respond(fail(70, "Song not found."), f)
        try:
            rating = int(params.get("rating") or 0)
        except (TypeError, ValueError):
            rating = 0
        playback_service.set_favorite(db, user.id, vf.id, rating >= 4)
        return ok()

    if method in ("scrobble",):
        vf = _get_song(db, params.get("id"))
        if not vf:
            return _respond(fail(70, "Song not found."), f)
        submission = (params.get("submission") or "true").lower() not in ("false", "0", "no")
        try:
            time_ms = int(params.get("time") or 0)
        except (TypeError, ValueError):
            time_ms = 0
        alb = None
        if vf.folder_id:
            alb = db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))
        album_title = alb.title if alb else None
        artist, track, album = lastfm_service.parse_track_meta(
            vf.name, _file_artist_name(db, vf), None, album_title
        )
        if submission:
            pos = playback_service.get_position(db, user.id, vf.id)
            total = pos.duration_sec if pos else None
            playback_service.add_history(
                db,
                user.id,
                vf.id,
                duration_played_sec=float(total or 0),
                track_duration_sec=total,
                completed=True,
            )
            try:
                lastfm_service.scrobble(
                    artist,
                    track,
                    album,
                    timestamp=(time_ms // 1000) if time_ms > 0 else None,
                )
            except Exception:
                pass
        else:
            try:
                lastfm_service.update_now_playing(artist, track, album)
            except Exception:
                pass
        return ok()

    if method in ("getLyrics",):
        artist = params.get("artist")
        title = params.get("title") or params.get("track")
        data = None
        if title:
            data = lyrics_service.get_lyrics(artist=artist, track=title, album=params.get("album"))
        value = ""
        if data:
            value = (data.get("plain") or data.get("synced") or "").strip()
        return ok(
            "lyrics",
            {
                "artist": artist or "",
                "title": title or "",
                "value": value,
            },
        )

    if method in ("getLyricsBySongId",):
        vf = _get_song(db, params.get("id"))
        if not vf:
            return _respond(fail(70, "Song not found."), f)
        alb = None
        if vf.folder_id:
            alb = db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))
        artist_name = _file_artist_name(db, vf)
        base = (vf.name or "").rsplit("/", 1)[-1]
        if "." in base:
            base = base.rsplit(".", 1)[0]
        parsed_artist, parsed_track, _ = lastfm_service.parse_track_meta(
            vf.name, artist_name, None, alb.title if alb else None
        )
        data = lyrics_service.get_lyrics(
            artist=parsed_artist,
            track=parsed_track,
            album=alb.title if alb else None,
            file_name=vf.name,
        )
        structured = []
        if data:
            plain = (data.get("plain") or "").strip()
            synced = (data.get("synced") or "").strip()
            if plain:
                structured.append(
                    {
                        "format": "plain",
                        "line": [{"value": ln} for ln in plain.splitlines()],
                        "synced": False,
                        "displayArtist": parsed_artist or "",
                        "displayTitle": parsed_track or base,
                    }
                )
            if synced:
                lrc_lines = _parse_lrc(synced)
                if lrc_lines:
                    structured.append(
                        {
                            "format": "lrc",
                            "line": lrc_lines,
                            "synced": True,
                            "displayArtist": parsed_artist or "",
                            "displayTitle": parsed_track or base,
                        }
                    )
        return ok("structuredLyrics", structured)

    if method in ("getSimilarSongs", "getSimilarSongs2"):
        limit = 10
        try:
            limit = max(1, min(int(params.get("count") or 10), 100))
        except (TypeError, ValueError):
            pass
        vf = _get_song(db, params.get("id"))
        if not vf:
            return _respond(fail(70, "Song not found."), f)
        artist_name = _file_artist_name(db, vf)
        similar_names = []
        if artist_name:
            try:
                info = lastfm_service.get_artist_info(artist_name)
                if info:
                    similar_names = info.get("similar") or []
            except Exception:
                similar_names = []
        rows = []
        if similar_names:
            pattern_list = [n.lower() for n in similar_names if n]
            if pattern_list:
                artists = list(
                    db.scalars(
                        select(MusicArtist)
                        .where(func.lower(MusicArtist.display_name).in_(pattern_list))
                        .limit(limit)
                    ).all()
                )
                for a in artists:
                    for song in _artist_songs(db, a, limit=limit):
                        rows.append(song)
                        if len(rows) >= limit:
                            break
                    if len(rows) >= limit:
                        break
        if not rows:
            rows = _random_song_rows(db, limit)
        return ok("similarSongs", rows[:limit])

    if method in ("getTopSongs",):
        try:
            limit = max(1, min(int(params.get("count") or 10), 100))
        except (TypeError, ValueError):
            limit = 10
        return ok("topSongs", _random_song_rows(db, limit))

    if method in ("getArtistInfo", "getArtistInfo2"):
        artist = None
        if method == "getArtistInfo2":
            kind, num = _parse_id(params.get("id"))
            if num is not None:
                artist = _catalog_artist(db, num)
        else:
            name = (params.get("artist") or "").strip()
            if name:
                artist = db.scalar(
                    select(MusicArtist).where(func.lower(MusicArtist.display_name) == name.lower())
                )
        if not artist:
            return _respond(fail(70, "Artist not found."), f)
        bio = artist.bio or ""
        similar_names: list = []
        tags: list = []
        if lastfm_service.metadata_ready():
            try:
                info = lastfm_service.get_artist_info(artist.display_name)
                if info:
                    bio = bio or info.get("summary") or ""
                    similar_names = info.get("similar") or []
                    tags = info.get("tags") or []
            except Exception:
                pass
        payload = {
            "id": f"ar-{artist.id}",
            "name": artist.display_name,
            "coverArt": f"ar-{artist.id}",
            "albumCount": db.scalar(
                select(func.count(MusicAlbum.id)).where(MusicAlbum.artist_id == artist.id)
            )
            or 0,
            "biography": bio,
            "mbid": artist.mbid,
            "similarArtist": [
                {"id": None, "name": n, "albumCount": 0, "coverArt": None}
                for n in similar_names[:10]
            ],
            "externalInfo": {
                "tags": tags,
            },
        }
        return ok("artistInfo", payload)

    if method in ("getAlbumInfo",):
        kind, num = _parse_id(params.get("id"))
        if num is None:
            return _respond(fail(70, "Album not found."), f)
        alb = db.scalar(select(MusicAlbum).where(MusicAlbum.id == num))
        if not alb:
            return _respond(fail(70, "Album not found."), f)
        artist = _catalog_artist(db, alb.artist_id)
        payload = album_entry(
            alb,
            artist=artist.display_name if artist else "Unknown",
            artist_id=alb.artist_id,
        )
        payload["notes"] = ""
        return ok("albumInfo", payload)

    if method in ("getNowPlayingList",):
        return ok("nowPlayingList", [])

    if method in ("getGenres",):
        return ok("genres", [])

    if method in ("createShare",):
        ids = _multi_ids(request, params, "id")
        if not ids:
            return _respond(fail(10, "At least one song ID is required."), f)
        from app.api.share_router import create_share_impl

        shares = []
        for sid in ids:
            vf = _get_song(db, sid)
            if not vf:
                continue
            share = create_share_impl(db, user.id, vf.id)
            shares.append(
                {
                    "id": str(share.id),
                    "url": share.token,
                    "description": vf.name,
                    "created": ts(share.created_at),
                    "entry": [_file_song(db, vf)],
                }
            )
        if not shares:
            return _respond(fail(70, "Media not found."), f)
        return ok("share", shares[0])

    if method in ("getShares",):
        rows = db.execute(
            select(ShareLink, VFile)
            .outerjoin(VFile, VFile.id == ShareLink.file_id)
            .where(ShareLink.user_id == user.id)
            .order_by(ShareLink.created_at.desc())
        ).all()
        entries = []
        for share, vf in rows:
            if not vf:
                continue
            entries.append(
                {
                    "id": str(share.id),
                    "url": share.token,
                    "description": vf.name,
                    "created": ts(share.created_at),
                    "entry": [_file_song(db, vf)],
                }
            )
        return ok("shares", entries)

    return _respond(fail(0, f"Method '{method}' is not implemented by T-Music."), f)


@router.api_route("/rest/{method_path:path}", methods=["GET", "POST"])
async def subsonic_entry(
    method_path: str,
    request: Request,
    db: Session = Depends(get_db),
    u: Optional[str] = Query(None),
    p: Optional[str] = Query(None),
    t: Optional[str] = Query(None),
    s: Optional[str] = Query(None),
    c: Optional[str] = Query(None),
    v: Optional[str] = Query(None),
    f: Optional[str] = Query(None),
):
    # Accept /rest/ping.view and /rest/ping
    method = unquote(method_path or "ping")
    # Merge all query params for methods that need extras
    params = {k: request.query_params.get(k) for k in request.query_params.keys()}
    if "p" in params and params["p"] is not None:
        params["p"] = unquote(params["p"])
    return await _handle(method, request, db, params, f)
