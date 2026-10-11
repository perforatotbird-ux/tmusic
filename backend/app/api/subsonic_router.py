"""
T-Music Subsonic / OpenSubsonic REST API.

Mount: /rest/*
Clients: Ultrasonic, Symfonium, DSub, Subtracks, etc.
Auth query params: u, p (password/token) OR t=md5(token+salt)&s=salt, c, v, f=json|xml
"""
import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.password_work import run_password_work
from app.core.database import get_db
from app.models.auth import AppUser
from app.models.playlist import Playlist
from app.repositories.file_repository import FileRepository
from app.services import lastfm_service, lyrics_service, subsonic_auth
from app.services.subsonic_service import (
    album_by_id,
    album_for_track,
    album_info,
    album_list_entries,
    album_payload,
    artist_albums,
    artist_by_name,
    artist_index,
    artist_info_payload,
    catalog_artist as _catalog_artist,
    file_artist_name as _file_artist_name,
    file_song as _file_song,
    get_song as _get_song,
    parse_id as _parse_id,
    playlist_by_id,
    playlist_song_count,
    playlist_songs,
    random_song_rows as _random_song_rows,
    replace_playlist_items,
    search_result,
    similar_songs,
    user_playlists,
    user_shares,
)
from app.services.playback_service import playback_service
from app.services.stream_service import stream_service
from app.services.subsonic_response import (
    artist_entry,
    dict_to_subsonic_xml,
    fail,
    ok_envelope,
    playlist_entry,
    ts,
)

logger = logging.getLogger("tmusic.subsonic")
router = APIRouter(tags=["subsonic"])

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


def _multi_ids(request: Request, params: Dict[str, Any], key: str = "id") -> list:
    vals = request.query_params.getlist(key) or request.query_params.getlist(f"{key}[]")
    if not vals and params.get(key):
        vals = [params.get(key)]
    return vals


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
    user = await run_password_work(_http_auth_user,
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
        indexes = artist_index(db)
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
        album_list = artist_albums(db, artist)
        payload = artist_entry(artist, album_count=len(album_list))
        payload["album"] = album_list
        return ok("artist", payload)

    if method in ("getAlbum", "getAlbumInfo2"):
        _kind, num = _parse_id(params.get("id"))
        if num is None:
            return _respond(fail(70, "Album not found."), f)
        payload = album_payload(db, num)
        if payload is None:
            return _respond(fail(70, "Album not found."), f)
        return ok("album", payload)

    if method in ("getSong",):
        _kind, num = _parse_id(params.get("id"))
        if num is None:
            return _respond(fail(70, "Song not found."), f)
        vf = FileRepository.get_by_id(db, num)
        if not vf:
            return _respond(fail(70, "Song not found."), f)
        return ok("song", _file_song(db, vf))

    if method in ("getAlbumList", "getAlbumList2"):
        try:
            size = int(params.get("size") or 50)
        except (TypeError, ValueError):
            size = 50
        size = max(1, min(size, 500))
        type_ = (params.get("type") or "alphabeticalByArtist").lower()
        entries = album_list_entries(db, type_, size)
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
            song_count = int(params.get("songCount", 20))
            album_count = int(params.get("albumCount", 10))
            artist_count = int(params.get("artistCount", 10))
        except (TypeError, ValueError):
            return _respond(fail(10, "Invalid search count."), f)
        if any(n < 0 or n > 500 for n in (song_count, album_count, artist_count)):
            return _respond(fail(10, "Search counts must be between 0 and 500."), f)
        if not query:
            key = "searchResult3" if method == "search3" else "searchResult2"
            return ok(key, {})
        result = search_result(db, query, song_count, album_count, artist_count)
        key = "searchResult3" if method == "search3" else "searchResult2"
        return ok(key, result)

    if method in ("getPlaylists",):
        return ok("playlists", user_playlists(db, user.id))

    if method in ("getPlaylist",):
        kind, num = _parse_id(params.get("id"))
        if num is None:
            return _respond(fail(70, "Playlist not found."), f)
        pl = playlist_by_id(db, num, user.id)
        if not pl:
            return _respond(fail(70, "Playlist not found."), f)
        songs = playlist_songs(db, pl)
        payload = playlist_entry(pl, song_count=len(songs))
        payload["entry"] = songs
        return ok("playlist", payload)

    if method in ("createPlaylist",):
        name = str(params.get("name") or "Playlist").strip()
        if not name or len(name) > 120:
            return _respond(fail(10, "Playlist name must contain 1 to 120 characters."), f)
        _kind, playlist_num = _parse_id(params.get("playlistId")) if params.get("playlistId") else (None, None)
        song_ids = request.query_params.getlist("songId") or request.query_params.getlist("songId[]")
        if params.get("playlistId"):
            pl = playlist_by_id(db, playlist_num, user.id, lock=True)
        else:
            pl = Playlist(user_id=user.id, name=name)
            db.add(pl)
            db.commit()
            db.refresh(pl)
        if not pl:
            return _respond(fail(50, "Playlist not found."), f)
        if song_ids:
            replace_playlist_items(db, pl, song_ids)
            db.commit()
        return ok("playlist", playlist_entry(pl, song_count=playlist_song_count(db, pl)))

    if method in ("updatePlaylist",):
        _kind, num = _parse_id(params.get("playlistId"))
        pl = playlist_by_id(db, num, user.id, lock=True)
        if not pl:
            return _respond(fail(70, "Playlist not found."), f)
        name = params.get("name")
        if name is not None:
            name = str(name).strip()
            if not name or len(name) > 120:
                return _respond(fail(10, "Playlist name must contain 1 to 120 characters."), f)
            pl.name = name
        song_ids = request.query_params.getlist("songId") or request.query_params.getlist("songId[]")
        if song_ids:
            replace_playlist_items(db, pl, song_ids)
        db.commit()
        return ok("playlist", playlist_entry(pl))

    if method in ("deletePlaylist",):
        _kind, num = _parse_id(params.get("id"))
        pl = playlist_by_id(db, num, user.id, lock=True)
        if not pl:
            return _respond(fail(70, "Playlist not found."), f)
        db.delete(pl)
        db.commit()
        return ok()

    if method in ("stream", "download"):
        kind, num = _parse_id(params.get("id"))
        if num is None:
            return _respond(fail(70, "Media not found."), f)
        track = stream_service.snapshot_track(stream_service.get_track_or_404(db, num))
        if db is not None:
            db.close()
        return await stream_service.build_stream_response(request, track)

    if method in ("getCoverArt",):
        kind, num = _parse_id(params.get("id"))
        # Prefer album cover via existing proxy when possible; empty 1x1 otherwise
        if kind == "al" and num:
            alb = album_by_id(db, num)
            if alb and alb.custom_image_key:
                from fastapi.responses import RedirectResponse

                return RedirectResponse(url=f"/api/library/cover?key={alb.custom_image_key}&custom=1")
            if alb and alb.cover_url:
                from fastapi.responses import RedirectResponse

                from app.services.cover_cache_service import cover_cache

                return RedirectResponse(
                    url=f"/api/library/cover?key={cover_cache.url_to_key(alb.cover_url)}"
                )
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
        alb = album_for_track(db, vf)
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
                logger.exception("Last.fm scrobble failed; local history remains saved")
        else:
            try:
                lastfm_service.update_now_playing(artist, track, album)
            except Exception:
                logger.exception("Last.fm now-playing failed")
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
        alb = album_for_track(db, vf)
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
        return ok("similarSongs", similar_songs(db, vf, limit))

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
                artist = artist_by_name(db, name)
        if not artist:
            return _respond(fail(70, "Artist not found."), f)
        return ok("artistInfo", artist_info_payload(db, artist))

    if method in ("getAlbumInfo",):
        _kind, num = _parse_id(params.get("id"))
        if num is None:
            return _respond(fail(70, "Album not found."), f)
        payload = album_info(db, num)
        if payload is None:
            return _respond(fail(70, "Album not found."), f)
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
        return ok("shares", user_shares(db, user.id))

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
