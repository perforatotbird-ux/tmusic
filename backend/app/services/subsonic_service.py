"""
T-Music Subsonic domain layer.

Query / aggregation helpers for the /rest/* adapter. They live here (not in the
router) so the controller only routes + formats, while DB access and domain
shaping stay in the service layer.
"""
import logging
import re
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, load_only

from app.models.music import MusicAlbum, MusicArtist
from app.models.playlist import Playlist, PlaylistItem
from app.models.share import ShareLink
from app.models.vfs import FolderClosure, VFile
from app.repositories.file_repository import (
    FileRepository,
    _TRACK_LIST_COLUMNS,
    _natural_sort_columns,
)
from app.services import lastfm_service
from app.services.subsonic_response import album_entry, artist_entry, playlist_entry, song_entry, ts
from app.utils.like import escape_like

logger = logging.getLogger("tmusic.subsonic")

_ID_RE = re.compile(r"^(ar|al|fl|pl)-(\d+)$")


def parse_id(raw: Any) -> Tuple[Optional[str], Optional[int]]:
    """Split a Subsonic id (`ar-1`/`al-2`/`fl-3`/`pl-4` or a bare int)."""
    if raw is None:
        return None, None
    s = str(raw).strip()
    m = _ID_RE.match(s)
    if m:
        return m.group(1), int(m.group(2))
    if s.isdigit():
        return None, int(s)
    return None, None


def catalog_artist(db: Session, artist_id: int) -> Optional[MusicArtist]:
    """Only id + display_name are read; avoids pulling the heavy bio/wiki Text."""
    return db.scalar(
        select(MusicArtist)
        .options(load_only(MusicArtist.id, MusicArtist.display_name))
        .where(MusicArtist.id == artist_id)
    )


def album_by_id(db: Session, album_id: int) -> Optional[MusicAlbum]:
    return db.scalar(select(MusicAlbum).where(MusicAlbum.id == album_id))


def artist_by_name(db: Session, name: str) -> Optional[MusicArtist]:
    """Case-insensitive artist lookup by display name (getArtistInfo by name)."""
    return db.scalar(select(MusicArtist).where(func.lower(MusicArtist.display_name) == name.lower()))


def album_for_track(db: Session, vf: VFile) -> Optional[MusicAlbum]:
    """Album owning a track (via its folder), or None."""
    if not vf.folder_id:
        return None
    return db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))


def album_songs(db: Session, album: MusicAlbum) -> list:
    artist = catalog_artist(db, album.artist_id)
    tracks = FileRepository.list_tracks_recursive(db, album.folder_id, limit=1000)
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


def artist_songs(db: Session, artist: MusicArtist, limit: int = 500) -> list:
    """Tracks of an artist across albums, in one bounded SQL query.

    Previous implementation called `album_songs` per album (artist lookup +
    recursive track query each), materialising the whole discography before
    truncating it. This joins albums to their folder subtrees through the
    closure table and applies LIMIT in the database (PERF-005).
    """
    album_rows = (
        select(
            MusicAlbum.id.label("album_id"),
            MusicAlbum.title.label("album_title"),
            MusicAlbum.artist_id.label("album_artist_id"),
            MusicAlbum.folder_id.label("album_folder_id"),
            MusicAlbum.year.label("album_year"),
        )
        .where(MusicAlbum.artist_id == artist.id)
        .subquery()
    )
    prefix, num_part = _natural_sort_columns()
    stmt = (
        select(
            VFile,
            album_rows.c.album_id,
            album_rows.c.album_title,
            album_rows.c.album_artist_id,
            album_rows.c.album_year,
        )
        .options(load_only(*_TRACK_LIST_COLUMNS))
        .join(FolderClosure, VFile.folder_id == FolderClosure.descendant_id)
        .join(album_rows, album_rows.c.album_folder_id == FolderClosure.ancestor_id)
        .where(
            FolderClosure.ancestor_id.in_(
                select(MusicAlbum.folder_id).where(MusicAlbum.artist_id == artist.id)
            ),
            VFile.is_trash.is_(False),
            VFile.mime_type.like("audio/%"),
        )
        .order_by(
            album_rows.c.album_year,
            album_rows.c.album_title,
            FolderClosure.depth.asc(),
            prefix.asc(),
            num_part.asc().nulls_last(),
            VFile.name.asc(),
        )
        .limit(limit)
    )

    out = []
    current_album = None
    track_no = 0
    for vf, album_id, album_title, album_artist_id, _year in db.execute(stmt).all():
        if album_id != current_album:
            current_album = album_id
            track_no = 0
        track_no += 1
        out.append(
            song_entry(
                vf,
                artist=artist.display_name,
                album=album_title,
                artist_id=album_artist_id,
                album_id=album_id,
                track_no=track_no,
            )
        )
    return out


def random_song_rows(db: Session, size: int) -> list:
    # One join instead of a per-track album + artist lookup (was N+1).
    stmt = (
        select(VFile, MusicAlbum, MusicArtist)
        .outerjoin(MusicAlbum, MusicAlbum.folder_id == VFile.folder_id)
        .outerjoin(MusicArtist, MusicArtist.id == MusicAlbum.artist_id)
        .where(VFile.is_trash == False, VFile.mime_type.like("audio/%"))  # noqa: E712
        .order_by(func.random())
        .limit(max(1, min(size, 500)))
    )
    rows = []
    for vf, alb, artist in db.execute(stmt).all():
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


def file_song(db: Session, vf: VFile) -> Dict[str, Any]:
    alb = None
    artist = None
    if vf.folder_id:
        alb = db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))
        if alb:
            artist = catalog_artist(db, alb.artist_id)
    return song_entry(
        vf,
        artist=artist.display_name if artist else "Unknown",
        album=alb.title if alb else "Unknown",
        artist_id=alb.artist_id if alb else None,
        album_id=alb.id if alb else None,
    )


def file_artist_name(db: Session, vf: VFile) -> Optional[str]:
    if not vf.folder_id:
        return None
    alb = db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))
    if not alb:
        return None
    artist = catalog_artist(db, alb.artist_id)
    return artist.display_name if artist else None


def get_song(db: Session, raw_id: Any) -> Optional[VFile]:
    """Resolve a playable audio VFile from a Subsonic song id (or None)."""
    _kind, num = parse_id(raw_id)
    if num is None:
        return None
    vf = FileRepository.get_by_id(db, num)
    if not vf or vf.is_trash or not (vf.mime_type or "").startswith("audio/"):
        return None
    return vf


def album_payload(db: Session, album_id: int) -> Optional[Dict[str, Any]]:
    """Album card + songs for getAlbum (None when the album does not exist)."""
    alb = db.scalar(select(MusicAlbum).where(MusicAlbum.id == album_id))
    if not alb:
        return None
    artist = catalog_artist(db, alb.artist_id)
    songs = album_songs(db, alb)
    payload = album_entry(
        alb,
        artist=artist.display_name if artist else "Unknown",
        artist_id=alb.artist_id,
        song_count=len(songs),
    )
    payload["song"] = songs
    return payload


def search_result(db: Session, query: str, song_count: int, album_count: int, artist_count: int) -> Dict[str, Any]:
    """search2/search3 payload: matching artists, albums and songs."""
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
        db.scalars(select(MusicAlbum).where(MusicAlbum.title.ilike(pattern, escape="\\")).limit(album_count)).all()
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
    # One lookup for all album artists instead of one per album (was N+1).
    artist_ids = {alb.artist_id for alb in albums if alb.artist_id}
    artist_names = dict(
        db.execute(select(MusicArtist.id, MusicArtist.display_name).where(MusicArtist.id.in_(artist_ids))).all()
    ) if artist_ids else {}
    return {
        "artist": [artist_entry(a) for a in artists],
        "album": [
            album_entry(alb, artist=artist_names.get(alb.artist_id, "Unknown"), artist_id=alb.artist_id)
            for alb in albums
        ],
        "song": [file_song(db, vf) for vf in tracks],
    }


def similar_songs(db: Session, vf: VFile, limit: int) -> list:
    """Songs by artists Last.fm lists as similar (falls back to random songs)."""
    artist_name = file_artist_name(db, vf)
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
                for song in artist_songs(db, a, limit=limit):
                    rows.append(song)
                    if len(rows) >= limit:
                        break
                if len(rows) >= limit:
                    break
    if not rows:
        rows = random_song_rows(db, limit)
    return rows[:limit]


def album_info(db: Session, album_id: int) -> Optional[Dict[str, Any]]:
    """Album card for getAlbumInfo (None when the album does not exist)."""
    alb = db.scalar(select(MusicAlbum).where(MusicAlbum.id == album_id))
    if not alb:
        return None
    artist = catalog_artist(db, alb.artist_id)
    payload = album_entry(
        alb,
        artist=artist.display_name if artist else "Unknown",
        artist_id=alb.artist_id,
    )
    payload["notes"] = ""
    return payload


def artist_info_payload(db: Session, artist: MusicArtist) -> Dict[str, Any]:
    """getArtistInfo payload: local bio + Last.fm enrichment (best-effort)."""
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
            logger.debug("Last.fm artist info enrichment failed for %r", artist.display_name, exc_info=True)
    album_count = db.scalar(
        select(func.count(MusicAlbum.id)).where(MusicAlbum.artist_id == artist.id)
    ) or 0
    return {
        "id": f"ar-{artist.id}",
        "name": artist.display_name,
        "coverArt": f"ar-{artist.id}",
        "albumCount": album_count,
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


def artist_index(db: Session) -> list:
    """Letter-grouped artist index for getIndexes/getArtists (one GROUP BY count)."""
    artists = list(db.scalars(select(MusicArtist).order_by(MusicArtist.display_name)).all())
    album_counts = dict(
        db.execute(
            select(MusicAlbum.artist_id, func.count(MusicAlbum.id)).group_by(MusicAlbum.artist_id)
        ).all()
    )
    index_map: Dict[str, list] = {}
    for a in artists:
        entry = artist_entry(a, album_count=album_counts.get(a.id, 0))
        letter = (a.display_name or "#")[0].upper()
        if not letter.isalpha():
            letter = "#"
        index_map.setdefault(letter, []).append(entry)
    return [{"name": letter, "artist": index_map[letter]} for letter in sorted(index_map.keys())]


def artist_albums(db: Session, artist: MusicArtist) -> list:
    """Album cards for an artist with grouped song counts (one query, no N+1)."""
    albums = db.scalars(
        select(MusicAlbum).where(MusicAlbum.artist_id == artist.id).order_by(MusicAlbum.year, MusicAlbum.title)
    ).all()
    folder_ids = [alb.folder_id for alb in albums if alb.folder_id]
    song_counts = dict(
        db.execute(
            select(VFile.folder_id, func.count(VFile.id)).where(
                VFile.folder_id.in_(folder_ids),
                VFile.is_trash == False,  # noqa: E712
                VFile.mime_type.like("audio/%"),
            ).group_by(VFile.folder_id)
        ).all()
    ) if folder_ids else {}
    return [
        album_entry(alb, artist=artist.display_name, artist_id=artist.id, song_count=song_counts.get(alb.folder_id, 0))
        for alb in albums
    ]


def album_list_entries(db: Session, type_: str, size: int) -> list:
    """Album cards for getAlbumList/getAlbumList2 (artist names via one IN)."""
    stmt = select(MusicAlbum)
    if type_ in ("recent", "newest"):
        stmt = stmt.order_by(MusicAlbum.created_at.desc())
    elif type_ in ("random",):
        stmt = stmt.order_by(func.random())
    elif type_ in ("frequent", "recentlyplayed"):
        stmt = stmt.order_by(MusicAlbum.updated_at.desc())
    else:
        stmt = stmt.order_by(MusicAlbum.title)
    albums = list(db.scalars(stmt.limit(size)).all())
    artist_ids = {alb.artist_id for alb in albums if alb.artist_id}
    artist_names = dict(
        db.execute(
            select(MusicArtist.id, MusicArtist.display_name).where(MusicArtist.id.in_(artist_ids))
        ).all()
    ) if artist_ids else {}
    return [
        album_entry(alb, artist=artist_names.get(alb.artist_id, "Unknown"), artist_id=alb.artist_id)
        for alb in albums
    ]


def user_playlists(db: Session, user_id: int) -> list:
    """User playlists with song counts (one grouped count, no per-playlist query)."""
    rows = list(db.scalars(
        select(Playlist).where(Playlist.user_id == user_id).order_by(Playlist.updated_at.desc())
    ).all())
    counts = dict(
        db.execute(
            select(PlaylistItem.playlist_id, func.count(PlaylistItem.id))
            .where(PlaylistItem.playlist_id.in_([pl.id for pl in rows]))
            .group_by(PlaylistItem.playlist_id)
        ).all()
    ) if rows else {}
    return [playlist_entry(pl, song_count=counts.get(pl.id, 0)) for pl in rows]


def user_shares(db: Session, user_id: int) -> list:
    """getShares payload: user share links with their track entries."""
    rows = db.execute(
        select(ShareLink, VFile)
        .outerjoin(VFile, VFile.id == ShareLink.file_id)
        .where(ShareLink.user_id == user_id)
        .order_by(ShareLink.created_at.desc())
    ).all()
    entries = []
    for share, vf in rows:
        if not vf:
            continue
        entries.append({
            "id": str(share.id),
            "url": share.token,
            "description": vf.name,
            "created": ts(share.created_at),
            "entry": [file_song(db, vf)],
        })
    return entries


def playlist_by_id(db: Session, playlist_id: Optional[int], user_id: int, lock: bool = False) -> Optional[Playlist]:
    """Fetch a user-owned playlist (optionally row-locked for writes)."""
    if playlist_id is None:
        return None
    stmt = select(Playlist).where(Playlist.id == playlist_id, Playlist.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def replace_playlist_items(db: Session, pl: Playlist, song_ids: list) -> None:
    """Replace a playlist's items with the given song ids (unknown ids skipped)."""
    db.query(PlaylistItem).filter(PlaylistItem.playlist_id == pl.id).delete()
    pos = 1
    for sid in song_ids:
        _kind, num = parse_id(sid)
        if num is None:
            continue
        if not FileRepository.get_by_id(db, num):
            continue
        db.add(PlaylistItem(playlist_id=pl.id, file_id=num, position=pos))
        pos += 1


def playlist_song_count(db: Session, pl: Playlist) -> int:
    return db.scalar(select(func.count(PlaylistItem.id)).where(PlaylistItem.playlist_id == pl.id)) or 0


def playlist_songs(db: Session, pl: Playlist) -> list:
    """Ordered song entries for a playlist (album/artist resolved per track)."""
    items = list(
        db.execute(
            select(PlaylistItem, VFile)
            .outerjoin(VFile, VFile.id == PlaylistItem.file_id)
            .where(PlaylistItem.playlist_id == pl.id)
            .order_by(PlaylistItem.position, PlaylistItem.id)
        ).all()
    )
    songs = []
    for idx, (_item, vf) in enumerate(items, start=1):
        if not vf:
            continue
        alb = None
        artist = None
        if vf.folder_id:
            alb = db.scalar(select(MusicAlbum).where(MusicAlbum.folder_id == vf.folder_id))
            if alb:
                artist = catalog_artist(db, alb.artist_id)
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
    return songs
