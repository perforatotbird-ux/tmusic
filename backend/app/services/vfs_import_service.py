from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import sqlite3
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.vfs import Folder, FolderClosure, VFile
from app.models.music import MusicArtist, MusicAlbum
from app.repositories.album_repository import AlbumRepository
from app.repositories.artist_repository import ArtistRepository
from app.repositories.file_repository import FileRepository
from app.repositories.folder_repository import FolderRepository
from app.repositories.tg_account_repository import TGAccountRepository
from app.utils.time import utcnow

logger = logging.getLogger("tmusic.vfs.import")

MAX_IMPORT_BYTES = 256 * 1024 * 1024
# 2026-09-29: the compressed upload is capped above, but the DECOMPRESSED
# SQL stream was unbounded (zip-bomb → disk fill). 1 GiB of SQL text is
# orders of magnitude beyond a legitimate library dump (MAX_ROWS=1M anyway).
MAX_IMPORT_DECOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_ROWS = 1_000_000
MAX_LINE_BYTES = 2 * 1024 * 1024
PREVIEW_ITEM_LIMIT = 200
FOLDER_OPTION_LIMIT = 2000
SESSION_TTL_SECONDS = 60 * 60
MAX_SESSIONS = 4

_COPY_RE = re.compile(
    r"^COPY\s+(?:\"?public\"?\.)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s+\((.*)\)\s+FROM\s+stdin;\s*$",
    re.IGNORECASE,
)
_IMPORT_ID_RE = re.compile(r"^[a-f0-9]{32}$")
_ALLOWED_TABLES = frozenset({"folders", "folder_closures", "vfiles", "tg_accounts"})
_AUDIO_EXTENSIONS = frozenset(
    {
        ".aac",
        ".aiff",
        ".alac",
        ".flac",
        ".m4a",
        ".mp3",
        ".mp4",
        ".mpeg",
        ".mpga",
        ".oga",
        ".ogg",
        ".opus",
        ".wav",
        ".wma",
        ".wv",
    }
)


class VfsImportError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class SourceSnapshot:
    folders: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    closures: List[Tuple[int, int, int]] = field(default_factory=list)
    account_ids: Set[int] = field(default_factory=set)
    account_names: Dict[int, str] = field(default_factory=dict)
    table_counts: Dict[str, int] = field(default_factory=dict)
    copy_blocks: int = 0
    warnings: List[str] = field(default_factory=list)
    path_by_id: Dict[int, str] = field(default_factory=dict)
    depth_by_id: Dict[int, int] = field(default_factory=dict)


@dataclass
class ImportSession:
    import_id: str
    path: Optional[Path]
    sqlite_path: Optional[Path] = None
    display_name: str = "backup.sql"
    size_bytes: int = 0
    digest: str = ""
    actor_id: int = 0
    source_folder_id: Optional[int] = None
    source_folder_path: str = ""
    selected_folder_ids: Set[int] = field(default_factory=set)
    target_root_id: Optional[int] = None
    include_non_audio: bool = True
    snapshot: Optional[SourceSnapshot] = None
    preview: Optional[Dict[str, Any]] = None
    created_at: float = 0.0
    status: str = "ready"
    error: Optional[str] = None
    progress: Dict[str, Any] = field(default_factory=lambda: {
        "current": 0,
        "total": 0,
        "folders_created": 0,
        "folders_reused": 0,
        "files_imported": 0,
        "files_skipped": 0,
        "stage": "idle",
        "message": ""
    })
    result: Optional[Dict[str, Any]] = None


_SESSIONS: Dict[str, ImportSession] = {}
_SESSION_LOCK = threading.RLock()
_RUN_LOCK = threading.Lock()


def _staging_root() -> Path:
    root = settings.DATA_DIR / "vfs_imports"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _remove_file(path: Optional[Path]) -> None:
    if not path:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Unable to remove VFS import staging file")


def _cleanup_sessions() -> None:
    now = time.time()
    with _SESSION_LOCK:
        expired = [
            state
            for state in _SESSIONS.values()
            if state.status != "running" and now - state.created_at > SESSION_TTL_SECONDS
        ]
        for state in expired:
            _remove_file(state.path)
            _remove_file(state.sqlite_path)
            _SESSIONS.pop(state.import_id, None)
        removable = sorted(
            (state for state in _SESSIONS.values() if state.status != "running"),
            key=lambda state: state.created_at,
        )
        while len(_SESSIONS) >= MAX_SESSIONS and removable:
            state = removable.pop(0)
            _remove_file(state.path)
            _remove_file(state.sqlite_path)
            _SESSIONS.pop(state.import_id, None)
        known_paths = {state.path for state in _SESSIONS.values() if state.path}
        known_paths.update({state.sqlite_path for state in _SESSIONS.values() if state.sqlite_path})
        for pattern in ("*.sql", "*.db", "*.sqlite", "*.zip"):
            for candidate in _staging_root().glob(pattern):
                if candidate in known_paths:
                    continue
                try:
                    if candidate.stat().st_mtime < now - SESSION_TTL_SECONDS:
                        _remove_file(candidate)
                except OSError:
                    continue


def _safe_name(value: Optional[str], fallback: str = "backup.sql") -> str:
    raw = Path(value or fallback).name
    cleaned = "".join(ch for ch in raw if ch.isprintable() and ch not in '\\/:*?"<>|')
    return (cleaned or fallback)[:160]


def _stage_upload(upload: UploadFile) -> Tuple[Path, int, str]:
    root = _staging_root()
    import_id = uuid.uuid4().hex
    path = root / f"{import_id}.sql"
    digest = hashlib.sha256()
    total = 0
    try:
        upload.file.seek(0)
        with path.open("xb") as output:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_IMPORT_BYTES:
                    raise VfsImportError("SQL-файл превышает допустимый размер 256 МБ", 413)
                digest.update(chunk)
                output.write(chunk)
        if total == 0:
            raise VfsImportError("SQL-файл пуст")
        return path, total, digest.hexdigest()
    except Exception:
        _remove_file(path)
        raise


def _decode_copy_row(line: str, columns: List[str]) -> Dict[str, Any]:
    fields: List[Optional[str]] = []
    chars: List[str] = []
    null_field = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\t":
            fields.append(None if null_field and not chars else "".join(chars))
            chars = []
            null_field = False
            index += 1
            continue
        if char != "\\":
            chars.append(char)
            index += 1
            continue
        if index + 1 >= len(line):
            chars.append("\\")
            index += 1
            continue
        escaped = line[index + 1]
        index += 2
        if escaped == "N" and not chars and not null_field:
            null_field = True
        elif escaped == "b":
            chars.append("\b")
        elif escaped == "f":
            chars.append("\f")
        elif escaped == "n":
            chars.append("\n")
        elif escaped == "r":
            chars.append("\r")
        elif escaped == "t":
            chars.append("\t")
        elif escaped == "v":
            chars.append("\v")
        elif escaped == "\\":
            chars.append("\\")
        elif escaped in "01234567":
            start = index - 1
            end = start
            while end < min(start + 3, len(line)) and line[end] in "01234567":
                end += 1
            chars.append(chr(int(line[start:end], 8)))
            index = end
        elif escaped == "x":
            end = index
            while end < min(index + 2, len(line)) and line[end] in "0123456789abcdefABCDEF":
                end += 1
            if end > index:
                chars.append(chr(int(line[index:end], 16)))
                index = end
            else:
                chars.append("x")
        else:
            chars.append(escaped)
    fields.append(None if null_field and not chars else "".join(chars))
    if len(fields) != len(columns):
        raise VfsImportError("Некорректное число полей в COPY-блоке SQL")
    return dict(zip(columns, fields))


def _parse_copy_header(line: str) -> Optional[Tuple[str, List[str]]]:
    match = _COPY_RE.match(line)
    if not match:
        return None
    columns = [part.strip().strip('"') for part in match.group(2).split(",")]
    return match.group(1).lower(), columns


def _iter_copy_rows(handle: Any, columns: List[str]) -> Iterator[Dict[str, Any]]:
    count = 0
    terminated = False
    for raw_line in handle:
        if len(raw_line.encode("utf-8")) > MAX_LINE_BYTES:
            raise VfsImportError("Строка SQL-файла превышает допустимый размер")
        line = raw_line.rstrip("\r\n")
        if line == "\\.":
            terminated = True
            break
        if "\x00" in line:
            raise VfsImportError("SQL-файл содержит недопустимые байты")
        count += 1
        if count > MAX_ROWS:
            raise VfsImportError("SQL-файл содержит слишком много строк")
        yield _decode_copy_row(line, columns)
    if not terminated:
        raise VfsImportError("COPY-блок SQL-файла не завершён")


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise VfsImportError("Некорректное числовое поле в SQL-файле") from exc


def _bool_value(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "t", "true", "yes", "y"}


def _text_value(value: Any, limit: int = 10000) -> Optional[str]:
    if value is None:
        return None
    text = str(value).replace("\x00", "")
    return text[:limit]


def _datetime_value(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise VfsImportError("Некорректная дата в SQL-файле") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _account_map(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _account_ids(account_map: Dict[str, Any]) -> Set[int]:
    result: Set[int] = set()
    for key in account_map:
        try:
            value = int(str(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            result.add(value)
    return result


def _account_mapping(snapshot: SourceSnapshot, db: Session) -> Dict[int, int]:
    target_accounts = TGAccountRepository.list_all(db)
    target_ids = {account.id for account in target_accounts}
    target_by_name: Dict[str, List[int]] = {}
    for account in target_accounts:
        if account.name:
            target_by_name.setdefault(account.name.casefold(), []).append(account.id)
    mapping: Dict[int, int] = {}
    for source_id in snapshot.account_ids:
        if source_id in target_ids:
            mapping[source_id] = source_id
            continue
        candidates = target_by_name.get(snapshot.account_names.get(source_id, "").casefold(), [])
        if len(candidates) == 1:
            mapping[source_id] = candidates[0]
    return mapping


def _remap_account_map(account_map: Dict[str, Any], mapping: Dict[int, int]) -> Dict[str, Any]:
    remapped: Dict[str, Any] = {}
    for key, value in account_map.items():
        target_key = key
        try:
            source_key = int(str(key))
        except (TypeError, ValueError):
            source_key = None
        if source_key is not None and source_key in mapping:
            target_key = str(mapping[source_key])
        remapped[target_key] = value
    return remapped


def _scan_structure(path: Path) -> SourceSnapshot:
    with path.open("rb") as raw:
        prefix = raw.read(8192).lower()
    if not (b"postgresql database dump" in prefix or b"copy " in prefix or b"insert into" in prefix or b"create table" in prefix or b"sqlite" in prefix):
        raise VfsImportError("Поддерживается только SQL dump базы данных")
    snapshot = SourceSnapshot()
    with path.open("r", encoding="utf-8", errors="strict", newline="") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            if "\x00" in line:
                raise VfsImportError("SQL-файл содержит недопустимые байты")
            if re.match(r"^\s*COPY\b.*\bFROM\s+PROGRAM\b", line, re.IGNORECASE):
                raise VfsImportError("COPY FROM PROGRAM запрещён для VFS-импорта")
            if not line.upper().startswith("COPY "):
                continue
            parsed = _parse_copy_header(line)
            if not parsed:
                if "FROM stdin" in line:
                    raise VfsImportError("Обнаружен неподдерживаемый формат COPY-блока")
                continue
            table, columns = parsed
            rows = _iter_copy_rows(handle, columns)
            count = 0
            if table == "folders":
                for row in rows:
                    count += 1
                    folder_id = _int_or_none(row.get("id"))
                    if folder_id is None:
                        raise VfsImportError("Папка в SQL-файле не имеет id")
                    if folder_id in snapshot.folders:
                        raise VfsImportError("В SQL-файле повторяется идентификатор папки")
                    name = _text_value(row.get("name"), 1000)
                    if not name:
                        raise VfsImportError("В SQL-файле есть папка без имени")
                    snapshot.folders[folder_id] = {
                        "id": folder_id,
                        "name": name,
                        "parent_id": _int_or_none(row.get("parent_id")),
                        "created_at": _datetime_value(row.get("created_at")),
                        "updated_at": _datetime_value(row.get("updated_at")),
                        "is_trash": _bool_value(row.get("is_trash")),
                        "is_starred": _bool_value(row.get("is_starred")),
                        "color": _text_value(row.get("color"), 32),
                        "note": _text_value(row.get("note"), 5000),
                        "custom_icon": _text_value(row.get("custom_icon"), 1000),
                    }
            elif table == "folder_closures":
                for row in rows:
                    count += 1
                    ancestor = _int_or_none(row.get("ancestor_id"))
                    descendant = _int_or_none(row.get("descendant_id"))
                    depth = _int_or_none(row.get("depth"))
                    if ancestor is None or descendant is None or depth is None:
                        raise VfsImportError("Некорректная запись folder_closures")
                    snapshot.closures.append((ancestor, descendant, depth))
            elif table == "tg_accounts":
                for row in rows:
                    count += 1
                    account_id = _int_or_none(row.get("id"))
                    if account_id is not None:
                        snapshot.account_ids.add(account_id)
                        account_name = _text_value(row.get("name"), 300)
                        if account_name:
                            snapshot.account_names[account_id] = account_name
            elif table == "vfiles":
                for _ in rows:
                    count += 1
            else:
                for _ in rows:
                    count += 1
            snapshot.table_counts[table] = snapshot.table_counts.get(table, 0) + count
            snapshot.copy_blocks += 1
    required = {"folders", "folder_closures", "vfiles"}
    missing = sorted(required - set(snapshot.table_counts))
    if missing:
        raise VfsImportError(f"В SQL-файле отсутствуют таблицы: {', '.join(missing)}")
    ignored = sorted(set(snapshot.table_counts) - _ALLOWED_TABLES)
    if ignored:
        snapshot.warnings.append("Проигнорированы таблицы: " + ", ".join(ignored))
    if not snapshot.account_ids:
        snapshot.warnings.append("В SQL-файле нет Telegram accounts; проверьте совместимость сессий")
    _index_source(snapshot)
    return snapshot


def _index_source(snapshot: SourceSnapshot) -> None:
    paths: Dict[int, str] = {}
    depths: Dict[int, int] = {}
    for folder_id in snapshot.folders:
        chain: List[int] = []
        visiting: Set[int] = set()
        current = folder_id
        while current is not None and current not in paths:
            if current in visiting:
                raise VfsImportError("В исходном дереве папок обнаружен цикл")
            if current not in snapshot.folders:
                raise VfsImportError("В исходном дереве отсутствует родительская папка")
            if len(chain) >= 256:
                raise VfsImportError("Глубина исходного дерева превышает 256 папок")
            visiting.add(current)
            chain.append(current)
            current = snapshot.folders[current]["parent_id"]
        prefix = paths.get(current, "")
        depth = depths[current] + 1 if current is not None else 0
        if depth + len(chain) > 256:
            raise VfsImportError("Глубина исходного дерева превышает 256 папок")
        for item in reversed(chain):
            prefix = f"{prefix}/{snapshot.folders[item]['name']}" if prefix else snapshot.folders[item]["name"]
            paths[item] = prefix
            depths[item] = depth
            depth += 1
    snapshot.path_by_id = paths
    snapshot.depth_by_id = depths


def _normalise_path(value: Optional[str]) -> str:
    raw = (value or "Music").replace("\\", "/")
    parts = [part.strip() for part in raw.split("/") if part.strip()]
    if not parts or any(part in {".", ".."} for part in parts):
        raise VfsImportError("Укажите корректный путь папки источника")
    return "/".join(parts)


def _resolve_source_folder(snapshot: SourceSnapshot, path_value: Optional[str], folder_id: Optional[int]) -> Tuple[int, str]:
    if folder_id is not None:
        if folder_id not in snapshot.folders:
            raise VfsImportError("Выбранная папка источника не найдена")
        folder = snapshot.folders[folder_id]
        if folder["is_trash"]:
            raise VfsImportError("Выбранная папка источника находится в корзине")
        return folder_id, snapshot.path_by_id[folder_id]
    requested = _normalise_path(path_value)
    matches = [
        folder_id
        for folder_id, source_path in snapshot.path_by_id.items()
        if source_path.casefold() == requested.casefold()
    ]
    if not matches and requested.casefold() == "music":
        matches = [
            folder_id
            for folder_id, folder in snapshot.folders.items()
            if folder["name"].casefold() == "music" and not folder["is_trash"]
        ]
    if not matches:
        raise VfsImportError("Папка источника не найдена в SQL-файле")
    if len(matches) > 1:
        raise VfsImportError("Найдено несколько папок с таким именем; укажите полный путь")
    selected = matches[0]
    if snapshot.folders[selected]["is_trash"]:
        raise VfsImportError("Выбранная папка источника находится в корзине")
    return selected, snapshot.path_by_id[selected]


def _select_subtree(snapshot: SourceSnapshot, root_id: int) -> Set[int]:
    children: Dict[int, List[int]] = {}
    for folder in snapshot.folders.values():
        if folder["parent_id"] in snapshot.folders:
            children.setdefault(folder["parent_id"], []).append(folder["id"])
    selected: Set[int] = set()
    stack = [root_id]
    while stack:
        folder_id = stack.pop()
        if folder_id in selected or folder_id not in snapshot.folders:
            continue
        folder = snapshot.folders[folder_id]
        if folder["is_trash"]:
            continue
        selected.add(folder_id)
        stack.extend(children.get(folder_id, []))
    return selected


def _target_root(db: Session, target_root_id: Optional[int]) -> Optional[Folder]:
    if target_root_id is None:
        return None
    root = FolderRepository.get(db, target_root_id)
    if not root or root.is_trash:
        raise VfsImportError("Целевая папка T-Music не найдена")
    return root


def _target_index(db: Session) -> Dict[Tuple[Optional[int], str], List[Folder]]:
    result: Dict[Tuple[Optional[int], str], List[Folder]] = {}
    for folder in FolderRepository.list_all(db):
        if folder.is_trash or not folder.name:
            continue
        result.setdefault((folder.parent_id, folder.name.casefold()), []).append(folder)
    return result


def _plan_folders(
    db: Session,
    snapshot: SourceSnapshot,
    selected_ids: Set[int],
    source_root_id: int,
    target_root_id: Optional[int],
) -> Tuple[Dict[int, Optional[int]], Dict[int, str], Set[int]]:
    _target_root(db, target_root_id)
    index = _target_index(db)
    mapping: Dict[int, Optional[int]] = {}
    actions: Dict[int, str] = {}
    conflicts: Set[int] = set()
    ordered = sorted(selected_ids, key=lambda item: (snapshot.depth_by_id.get(item, 0), item))
    for folder_id in ordered:
        folder = snapshot.folders[folder_id]
        if folder_id == source_root_id:
            parent_id = target_root_id
        else:
            parent_id = mapping.get(folder["parent_id"])
            if parent_id is None and folder["parent_id"] not in mapping:
                conflicts.add(folder_id)
                mapping[folder_id] = None
                actions[folder_id] = "conflict"
                continue
        candidates = index.get((parent_id, folder["name"].casefold()), [])
        if len(candidates) == 1:
            mapping[folder_id] = candidates[0].id
            actions[folder_id] = "reuse"
        elif len(candidates) > 1:
            mapping[folder_id] = None
            actions[folder_id] = "conflict"
            conflicts.add(folder_id)
        else:
            mapping[folder_id] = None
            actions[folder_id] = "create"
    return mapping, actions, conflicts


def _existing_names(db: Session, folder_ids: List[int]) -> Dict[int, Set[str]]:
    result: Dict[int, Set[str]] = {folder_id: set() for folder_id in folder_ids}
    if not folder_ids:
        return result
    rows = FileRepository.names_in_folders(db, folder_ids)
    for folder_id, name in rows:
        if folder_id in result and name:
            result[folder_id].add(name.casefold())
    return result


def _is_audio(name: Optional[str], mime_type: Optional[str]) -> bool:
    if mime_type and mime_type.casefold().startswith("audio/"):
        return True
    if not name:
        return False
    extension = Path(name).suffix.casefold()
    return extension in _AUDIO_EXTENSIONS or (mimetypes.guess_type(name)[0] or "").startswith("audio/")


def _iter_vfiles(path: Path) -> Iterator[Dict[str, Any]]:
    found = False
    with path.open("r", encoding="utf-8", errors="strict", newline="") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            if re.match(r"^\s*COPY\b.*\bFROM\s+PROGRAM\b", line, re.IGNORECASE):
                raise VfsImportError("COPY FROM PROGRAM запрещён для VFS-импорта")
            parsed = _parse_copy_header(line)
            if not parsed:
                continue
            table, columns = parsed
            if table != "vfiles":
                for _ in _iter_copy_rows(handle, columns):
                    pass
                continue
            found = True
            yield from _iter_copy_rows(handle, columns)
    if not found:
        raise VfsImportError("В SQL-файле отсутствует таблица vfiles")


def _vfile_values(row: Dict[str, Any]) -> Dict[str, Any]:
    name = _text_value(row.get("name"), 1000)
    if not name:
        raise VfsImportError("В SQL-файле есть VFile без имени")
    mime_type = _text_value(row.get("mime_type"), 255)
    if not mime_type:
        mime_type = mimetypes.guess_type(name)[0]
    return {
        "name": name,
        "size": _int_or_none(row.get("size")),
        "mime_type": mime_type,
        "tg_message_id": _int_or_none(row.get("tg_message_id")),
        "msg_ids": _text_value(row.get("msg_ids"), 2 * 1024 * 1024),
        "tg_file_id": _text_value(row.get("tg_file_id"), 2 * 1024 * 1024),
        "is_trash": _bool_value(row.get("is_trash")),
        "is_starred": _bool_value(row.get("is_starred")),
        "pending_deletion": _bool_value(row.get("pending_deletion")),
        "encryption_salt": _text_value(row.get("encryption_salt"), 2 * 1024 * 1024),
        "encryption_header": _text_value(row.get("encryption_header"), 2 * 1024 * 1024),
        "is_encrypted": _bool_value(row.get("is_encrypted")),
        "note": _text_value(row.get("note"), 5000),
        "custom_icon": _text_value(row.get("custom_icon"), 1000),
        "account_map": _account_map(row.get("account_map")),
        "created_at": _datetime_value(row.get("created_at")),
        "updated_at": _datetime_value(row.get("updated_at")),
    }


def _build_preview(
    db: Session,
    path: Path,
    snapshot: SourceSnapshot,
    source_root_id: int,
    source_folder_path: str,
    selected_ids: Set[int],
    target_root_id: Optional[int],
    include_non_audio: bool,
    display_name: str,
    size_bytes: int,
    digest: str,
) -> Dict[str, Any]:
    mapping, actions, folder_conflicts = _plan_folders(
        db, snapshot, selected_ids, source_root_id, target_root_id
    )
    existing_ids = [folder_id for folder_id in mapping.values() if folder_id is not None]
    existing_names = _existing_names(db, existing_ids)
    seen_source_names: Set[Tuple[Any, str]] = set()
    account_ids: Set[int] = set()
    items: List[Dict[str, Any]] = []
    counters = {
        "files_total": 0,
        "audio_files": 0,
        "files_imported": 0,
        "files_skipped_non_audio": 0,
        "files_skipped_trash": 0,
        "files_skipped_pending": 0,
        "files_conflicts": 0,
        "encrypted_files": 0,
        "unlocated_files": 0,
    }
    for row in _iter_vfiles(path):
        folder_id = _int_or_none(row.get("folder_id"))
        if folder_id not in selected_ids:
            continue
        counters["files_total"] += 1
        if _bool_value(row.get("is_trash")):
            counters["files_skipped_trash"] += 1
            continue
        if _bool_value(row.get("pending_deletion")):
            counters["files_skipped_pending"] += 1
            continue
        values = _vfile_values(row)
        is_audio = _is_audio(values["name"], values["mime_type"])
        if not is_audio and not include_non_audio:
            counters["files_skipped_non_audio"] += 1
            if len(items) < PREVIEW_ITEM_LIMIT:
                items.append({
                    "kind": "file",
                    "path": f"{snapshot.path_by_id.get(folder_id, '?')}/{values['name']}",
                    "action": "skip",
                    "status": "non_audio",
                    "mime_type": values["mime_type"],
                    "size": values["size"],
                })
            continue
        if is_audio:
            counters["audio_files"] += 1
        if values["is_encrypted"]:
            counters["encrypted_files"] += 1
        account_ids.update(_account_ids(values["account_map"]))
        if not values["tg_message_id"] and not values["msg_ids"] and not values["tg_file_id"]:
            counters["unlocated_files"] += 1
        target_id = mapping.get(folder_id)
        action = "create"
        status = "ready"
        if folder_id in folder_conflicts or target_id is None and folder_id in folder_conflicts:
            action = "conflict"
            status = "folder_conflict"
            counters["files_conflicts"] += 1
        else:
            key = (target_id if target_id is not None else f"new:{folder_id}", values["name"].casefold())
            if target_id is not None and key in existing_names[target_id] or key in seen_source_names:
                action = "skip"
                status = "conflict"
                counters["files_conflicts"] += 1
            else:
                seen_source_names.add(key)
                counters["files_imported"] += 1
        if len(items) < PREVIEW_ITEM_LIMIT:
            items.append({
                "kind": "file",
                "path": f"{snapshot.path_by_id.get(folder_id, '?')}/{values['name']}",
                "action": action,
                "status": status,
                "mime_type": values["mime_type"],
                "size": values["size"],
            })
    account_mapping = _account_mapping(snapshot, db)
    missing_accounts = sorted(account_ids - set(account_mapping))
    folder_items = [
        {
            "id": folder_id,
            "path": snapshot.path_by_id.get(folder_id, "?"),
            "action": actions.get(folder_id, "create"),
        }
        for folder_id in sorted(selected_ids, key=lambda item: snapshot.path_by_id.get(item, "").casefold())
    ]
    folder_options = [
        {"id": folder_id, "path": path_value}
        for folder_id, path_value in sorted(snapshot.path_by_id.items(), key=lambda item: item[1].casefold())
        if not snapshot.folders[folder_id]["is_trash"]
    ]
    warnings = list(snapshot.warnings)
    warnings.append("Импортируются метаданные и Telegram-ссылки; байты аудио в SQL-файле отсутствуют.")
    if counters["files_skipped_non_audio"]:
        warnings.append(f"Пропущено не-аудио файлов: {counters['files_skipped_non_audio']}.")
    if counters["files_conflicts"]:
        warnings.append(f"Конфликты будут пропущены: {counters['files_conflicts']}.")
    if missing_accounts:
        warnings.append("В T-Music нет TG-аккаунтов для части ссылок: " + ", ".join(map(str, missing_accounts[:20])))
    if counters["encrypted_files"]:
        warnings.append("Для зашифрованных файлов требуется совместимый encryption key T-Music.")
    if counters["unlocated_files"]:
        warnings.append("Часть VFile не содержит Telegram locator и потребует ручного заполнения.")
    return {
        "ok": True,
        "import_id": "",
        "status": "preview",
        "file": {"name": display_name, "size_bytes": size_bytes, "sha256": digest},
        "source": {
            "folder_id": source_root_id,
            "folder_path": source_folder_path,
            "folder_count": len(selected_ids),
            "table_counts": snapshot.table_counts,
            "copy_blocks": snapshot.copy_blocks,
        },
        "target": {"root_id": target_root_id},
        "summary": {
            **counters,
            "folders_create": sum(1 for action in actions.values() if action == "create"),
            "folders_reuse": sum(1 for action in actions.values() if action == "reuse"),
            "folders_conflict": len(folder_conflicts),
            "can_run": counters["files_imported"] > 0,
        },
        "folder_options": folder_options[:FOLDER_OPTION_LIMIT],
        "folder_options_total": len(folder_options),
        "folder_options_truncated": len(folder_options) > FOLDER_OPTION_LIMIT,
        "folder_items": folder_items[:PREVIEW_ITEM_LIMIT],
        "items": items,
        "items_total": counters["files_total"],
        "items_returned": len(items),
        "items_truncated": counters["files_total"] > len(items),
        "missing_accounts": missing_accounts[:100],
        "warnings": warnings,
        "errors": [],
    }


def _valid_import_id(import_id: str) -> bool:
    return bool(_IMPORT_ID_RE.fullmatch(import_id or ""))


def preview_vfs_import(
    db: Session,
    upload: UploadFile,
    source_folder_path: Optional[str],
    source_folder_id: Optional[int],
    target_root_id: Optional[int],
    include_non_audio: bool,
    actor_id: int,
) -> Dict[str, Any]:
    _cleanup_sessions()
    path, size_bytes, digest = _stage_upload(upload)
    try:
        snapshot = _scan_structure(path)
        source_root_id, resolved_path = _resolve_source_folder(
            snapshot, source_folder_path, source_folder_id
        )
        selected_ids = _select_subtree(snapshot, source_root_id)
        if not selected_ids:
            raise VfsImportError("Выбранная папка не содержит доступных подпапок")
        result = _build_preview(
            db,
            path,
            snapshot,
            source_root_id,
            resolved_path,
            selected_ids,
            target_root_id,
            include_non_audio,
            _safe_name(upload.filename),
            size_bytes,
            digest,
        )
        import_id = uuid.uuid4().hex
        result["import_id"] = import_id
        with _SESSION_LOCK:
            _cleanup_sessions()
            state = ImportSession(
                import_id=import_id,
                path=path,
                display_name=_safe_name(upload.filename),
                size_bytes=size_bytes,
                digest=digest,
                actor_id=actor_id,
                source_folder_id=source_root_id,
                source_folder_path=resolved_path,
                selected_folder_ids=selected_ids,
                target_root_id=target_root_id,
                include_non_audio=include_non_audio,
                snapshot=snapshot,
                preview=result,
                created_at=time.time(),
            )
            _SESSIONS[import_id] = state
        return result
    except Exception:
        _remove_file(path)
        raise


def _get_session(import_id: str, actor_id: int) -> ImportSession:
    if not _valid_import_id(import_id):
        raise VfsImportError("Некорректный идентификатор импорта")
    _cleanup_sessions()
    with _SESSION_LOCK:
        state = _SESSIONS.get(import_id)
        if not state or state.actor_id != actor_id:
            raise VfsImportError("Импорт не найден или истёк", 404)
        return state


def _rebuild_closures(db: Session) -> None:
    FolderRepository.delete_all_closures(db)
    folders = FolderRepository.list_all(db)
    parents = {folder.id: folder.parent_id for folder in folders}
    for folder in folders:
        current_id = folder.id
        depth = 0
        visited: Set[int] = set()
        while current_id is not None and current_id not in visited:
            visited.add(current_id)
            db.add(FolderClosure(ancestor_id=current_id, descendant_id=folder.id, depth=depth))
            current_id = parents.get(current_id)
            depth += 1
    db.flush()


def _import_folders(
    db: Session,
    snapshot: SourceSnapshot,
    selected_ids: Set[int],
    source_root_id: int,
    target_root_id: Optional[int],
) -> Tuple[Dict[int, int], Dict[str, int]]:
    _target_root(db, target_root_id)
    index = _target_index(db)
    mapping: Dict[int, int] = {}
    counts = {"created": 0, "reused": 0}
    for folder_id in sorted(selected_ids, key=lambda item: (snapshot.depth_by_id.get(item, 0), item)):
        source = snapshot.folders[folder_id]
        parent_id = target_root_id if folder_id == source_root_id else mapping.get(source["parent_id"])
        if folder_id != source_root_id and parent_id is None:
            raise VfsImportError("Нельзя определить родителя импортируемой папки")
        candidates = index.get((parent_id, source["name"].casefold()), [])
        if len(candidates) > 1:
            raise VfsImportError("В целевой VFS найдено несколько папок с одинаковым именем")
        if candidates:
            mapping[folder_id] = candidates[0].id
            counts["reused"] += 1
            continue
        folder = Folder(
            name=source["name"],
            parent_id=parent_id,
            is_trash=False,
            is_starred=source["is_starred"],
            color=source["color"],
            note=source["note"],
            custom_icon=source["custom_icon"],
            cached_total_size=0,
            cached_file_count=0,
            created_at=source["created_at"],
            updated_at=source["updated_at"],
        )
        db.add(folder)
        db.flush()
        mapping[folder_id] = folder.id
        index.setdefault((parent_id, source["name"].casefold()), []).append(folder)
        counts["created"] += 1
    return mapping, counts


def run_vfs_import(
    db: Session,
    import_id: str,
    actor_id: int,
    confirm: bool,
    before_commit: Optional[Callable[[Session, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    state = _get_session(import_id, actor_id)
    if not confirm:
        raise VfsImportError("Для запуска импорта требуется подтверждение")
    if not state.preview.get("summary", {}).get("can_run"):
        raise VfsImportError("Preview не содержит файлов для импорта")
    if not _RUN_LOCK.acquire(blocking=False):
        raise VfsImportError("Другой VFS-импорт уже выполняется", 409)
    try:
        with _SESSION_LOCK:
            if state.status == "running":
                raise VfsImportError("Импорт уже выполняется", 409)
            state.status = "running"
        path = state.path
        if not path or not path.is_file():
            raise VfsImportError("SQL-файл импорта уже удалён")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != state.digest:
            raise VfsImportError("SQL-файл изменился после preview")
        db.rollback()
        folder_mapping, folder_counts = _import_folders(
            db,
            state.snapshot,
            state.selected_folder_ids,
            state.source_folder_id,
            state.target_root_id,
        )
        _rebuild_closures(db)
        existing_ids = list(folder_mapping.values())
        existing_names = _existing_names(db, existing_ids)
        account_mapping = _account_mapping(state.snapshot, db)
        seen_names: Set[Tuple[Any, str]] = set()
        counters = {
            "files_imported": 0,
            "files_skipped_non_audio": 0,
            "files_skipped_trash": 0,
            "files_skipped_pending": 0,
            "files_conflicts": 0,
            "encrypted_files": 0,
        }
        batch: List[VFile] = []
        for row in _iter_vfiles(path):
            source_folder_id = _int_or_none(row.get("folder_id"))
            if source_folder_id not in state.selected_folder_ids:
                continue
            if _bool_value(row.get("is_trash")):
                counters["files_skipped_trash"] += 1
                continue
            if _bool_value(row.get("pending_deletion")):
                counters["files_skipped_pending"] += 1
                continue
            values = _vfile_values(row)
            if not _is_audio(values["name"], values["mime_type"]) and not state.include_non_audio:
                counters["files_skipped_non_audio"] += 1
                continue
            if values["is_encrypted"]:
                counters["encrypted_files"] += 1
            target_folder_id = folder_mapping[source_folder_id]
            name_key = values["name"].casefold()
            conflict_key = (target_folder_id, name_key)
            if name_key in existing_names[target_folder_id] or conflict_key in seen_names:
                counters["files_conflicts"] += 1
                continue
            seen_names.add(conflict_key)
            batch.append(VFile(
                name=values["name"],
                size=values["size"],
                mime_type=values["mime_type"],
                tg_message_id=values["tg_message_id"],
                msg_ids=values["msg_ids"],
                tg_file_id=values["tg_file_id"],
                folder_id=target_folder_id,
                is_starred=values["is_starred"],
                is_trash=False,
                pending_deletion=False,
                encryption_salt=values["encryption_salt"],
                encryption_header=values["encryption_header"],
                is_encrypted=values["is_encrypted"],
                note=values["note"],
                custom_icon=values["custom_icon"],
                account_map=_remap_account_map(values["account_map"], account_mapping),
                created_at=values["created_at"],
                updated_at=values["updated_at"],
            ))
            counters["files_imported"] += 1
            if len(batch) >= 500:
                db.add_all(batch)
                db.flush()
                batch.clear()
        if batch:
            db.add_all(batch)
            db.flush()
        result = {
            "ok": True,
            "import_id": import_id,
            "status": "completed",
            "source_folder_path": state.source_folder_path,
            "folders_created": folder_counts["created"],
            "folders_reused": folder_counts["reused"],
            **counters,
            "warnings": state.preview.get("warnings", []),
            "completed_at": utcnow().isoformat(),
        }
        if before_commit is not None:
            before_commit(db, result)
        db.commit()
        state.status = "completed"
        state.result = result
        state.error = None
        _remove_file(state.path)
        state.path = None
        return result
    except VfsImportError:
        db.rollback()
        with _SESSION_LOCK:
            state.status = "failed"
        raise
    except Exception as exc:
        db.rollback()
        with _SESSION_LOCK:
            state.status = "failed"
            state.error = type(exc).__name__
        logger.error("VFS import failed with %s", type(exc).__name__)
        raise VfsImportError("Импорт не выполнен: проверьте preview и целевую VFS", 500) from exc
    finally:
        _RUN_LOCK.release()


def get_vfs_import(import_id: str, actor_id: int) -> Dict[str, Any]:
    state = _get_session(import_id, actor_id)
    return {
        "import_id": state.import_id,
        "status": state.status,
        "error": state.error,
        "preview": state.preview,
        "progress": state.progress,
        "result": state.result,
    }


def get_import_status(import_id: str, actor_id: int) -> Dict[str, Any]:
    state = _get_session(import_id, actor_id)
    return {
        "ok": True,
        "import_id": state.import_id,
        "status": state.status,
        "progress": state.progress,
        "result": state.result,
        "error": state.error,
    }


def _parse_sql_tokens(values_str: str) -> List[Any]:
    s = values_str.strip()
    if s.startswith("("):
        s = s[1:]
    if s.endswith(")"):
        s = s[:-1]
    tokens: List[Any] = []
    i = 0
    n = len(s)
    while i < n:
        while i < n and s[i].isspace():
            i += 1
        if i >= n:
            break
        if s[i] == "'":
            chars: List[str] = []
            i += 1
            while i < n:
                if s[i] == "'":
                    if i + 1 < n and s[i + 1] == "'":
                        chars.append("'")
                        i += 2
                    else:
                        i += 1
                        break
                elif s[i] == "\\" and i + 1 < n:
                    chars.append(s[i + 1])
                    i += 2
                else:
                    chars.append(s[i])
                    i += 1
            tokens.append("".join(chars))
            while i < n and s[i].isspace():
                i += 1
            if i < n and s[i] == ",":
                i += 1
        else:
            start = i
            while i < n and s[i] != ",":
                i += 1
            raw = s[start:i].strip()
            if i < n and s[i] == ",":
                i += 1
            upper = raw.upper()
            if upper in ("NULL", "\\N", ""):
                tokens.append(None)
            elif upper in ("TRUE", "T"):
                tokens.append(1)
            elif upper in ("FALSE", "F"):
                tokens.append(0)
            else:
                try:
                    tokens.append(int(raw))
                except ValueError:
                    try:
                        tokens.append(float(raw))
                    except ValueError:
                        tokens.append(raw)
    return tokens


_INSERT_RE = re.compile(
    r"^INSERT\s+INTO\s+[\"']?(?:public\.)?([a-zA-Z0-9_]+)[\"']?\s*(?:\((.*?)\))?\s*VALUES\s*(.*);?\s*$",
    re.IGNORECASE,
)

_DEFAULT_FOLDER_COLS = [
    "id", "name", "parent_id", "created_at", "updated_at",
    "is_trash", "is_starred", "color", "note", "cached_total_size",
    "cached_file_count", "custom_icon"
]

_DEFAULT_VFILE_COLS = [
    "id", "name", "size", "mime_type", "tg_message_id",
    "msg_ids", "tg_file_id", "folder_id", "is_starred", "is_trash",
    "pending_deletion", "encryption_salt", "encryption_header",
    "is_encrypted", "note", "custom_icon", "account_map",
    "created_at", "updated_at"
]


def _init_staging_sqlite(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = 0")
    conn.execute("PRAGMA cache_size = 200000")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA locking_mode = EXCLUSIVE")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id INTEGER,
            is_trash INTEGER DEFAULT 0,
            is_starred INTEGER DEFAULT 0,
            color TEXT,
            note TEXT,
            custom_icon TEXT,
            created_at TEXT,
            updated_at TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vfiles (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            folder_id INTEGER,
            size INTEGER,
            mime_type TEXT,
            tg_message_id INTEGER,
            msg_ids TEXT,
            tg_file_id TEXT,
            is_trash INTEGER DEFAULT 0,
            is_starred INTEGER DEFAULT 0,
            pending_deletion INTEGER DEFAULT 0,
            is_encrypted INTEGER DEFAULT 0,
            encryption_salt TEXT,
            encryption_header TEXT,
            note TEXT,
            custom_icon TEXT,
            account_map TEXT,
            created_at TEXT,
            updated_at TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tg_accounts (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS folder_closures (
            ancestor_id INTEGER,
            descendant_id INTEGER,
            depth INTEGER
        );
    """)
    conn.commit()
    return conn


def _populate_staging_db(sql_path: Path, conn: sqlite3.Connection) -> Tuple[int, int]:
    folder_batch: List[Tuple] = []
    vfile_batch: List[Tuple] = []
    account_batch: List[Tuple] = []
    closure_batch: List[Tuple] = []
    total_folders_count = 0
    total_files_count = 0

    def flush_batches():
        if folder_batch:
            conn.executemany(
                "INSERT INTO folders (id, name, parent_id, is_trash, is_starred, color, note, custom_icon, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                folder_batch,
            )
            folder_batch.clear()
        if vfile_batch:
            conn.executemany(
                "INSERT INTO vfiles (id, name, folder_id, size, mime_type, tg_message_id, msg_ids, tg_file_id, is_trash, is_starred, pending_deletion, is_encrypted, encryption_salt, encryption_header, note, custom_icon, account_map, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                vfile_batch,
            )
            vfile_batch.clear()
        if account_batch:
            conn.executemany(
                "INSERT INTO tg_accounts (id, name) VALUES (?,?)",
                account_batch,
            )
            account_batch.clear()
        if closure_batch:
            conn.executemany(
                "INSERT INTO folder_closures (ancestor_id, descendant_id, depth) VALUES (?,?,?)",
                closure_batch,
            )
            closure_batch.clear()

    with sql_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        in_copy_table: Optional[str] = None
        col_map: Dict[str, int] = {}

        for raw_line in handle:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue

            if in_copy_table:
                if line == "\\.":
                    flush_batches()
                    in_copy_table = None
                    continue

                parts = line.split("\t")
                if in_copy_table == "folders":
                    fid_raw = parts[col_map["id"]] if "id" in col_map and col_map["id"] < len(parts) else None
                    if fid_raw and fid_raw != "\\N":
                        try:
                            fid = int(fid_raw)
                        except ValueError:
                            continue
                        name_raw = parts[col_map["name"]] if "name" in col_map and col_map["name"] < len(parts) else None
                        if name_raw and name_raw != "\\N":
                            name = name_raw.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r").replace("\\\\", "\\") if "\\" in name_raw else name_raw
                            pid_raw = parts[col_map["parent_id"]] if "parent_id" in col_map and col_map["parent_id"] < len(parts) else None
                            pid = int(pid_raw) if pid_raw and pid_raw != "\\N" else None
                            trash_raw = parts[col_map["is_trash"]] if "is_trash" in col_map and col_map["is_trash"] < len(parts) else "f"
                            is_trash = 1 if trash_raw in ("t", "1", "true", "TRUE") else 0
                            star_raw = parts[col_map["is_starred"]] if "is_starred" in col_map and col_map["is_starred"] < len(parts) else "f"
                            is_star = 1 if star_raw in ("t", "1", "true", "TRUE") else 0
                            color = parts[col_map["color"]] if "color" in col_map and col_map["color"] < len(parts) and parts[col_map["color"]] != "\\N" else None
                            note = parts[col_map["note"]] if "note" in col_map and col_map["note"] < len(parts) and parts[col_map["note"]] != "\\N" else None
                            icon = parts[col_map["custom_icon"]] if "custom_icon" in col_map and col_map["custom_icon"] < len(parts) and parts[col_map["custom_icon"]] != "\\N" else None
                            cat = parts[col_map["created_at"]] if "created_at" in col_map and col_map["created_at"] < len(parts) and parts[col_map["created_at"]] != "\\N" else ""
                            uat = parts[col_map["updated_at"]] if "updated_at" in col_map and col_map["updated_at"] < len(parts) and parts[col_map["updated_at"]] != "\\N" else ""
                            folder_batch.append((fid, name[:1000], pid, is_trash, is_star, color, note, icon, cat, uat))
                            if not is_trash:
                                total_folders_count += 1
                            if len(folder_batch) >= 15000:
                                flush_batches()
                elif in_copy_table == "vfiles":
                    vid_raw = parts[col_map["id"]] if "id" in col_map and col_map["id"] < len(parts) else None
                    if vid_raw and vid_raw != "\\N":
                        try:
                            vid = int(vid_raw)
                        except ValueError:
                            continue
                        name_raw = parts[col_map["name"]] if "name" in col_map and col_map["name"] < len(parts) else None
                        if name_raw and name_raw != "\\N":
                            name = name_raw.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r").replace("\\\\", "\\") if "\\" in name_raw else name_raw
                            f_raw = parts[col_map["folder_id"]] if "folder_id" in col_map and col_map["folder_id"] < len(parts) else None
                            folder_id = int(f_raw) if f_raw and f_raw != "\\N" else None
                            sz_raw = parts[col_map["size"]] if "size" in col_map and col_map["size"] < len(parts) else None
                            size = int(sz_raw) if sz_raw and sz_raw != "\\N" else None
                            mime_raw = parts[col_map["mime_type"]] if "mime_type" in col_map and col_map["mime_type"] < len(parts) and parts[col_map["mime_type"]] != "\\N" else None
                            mime = mime_raw or "audio/mpeg"
                            mid_raw = parts[col_map["tg_message_id"]] if "tg_message_id" in col_map and col_map["tg_message_id"] < len(parts) else None
                            tg_mid = int(mid_raw) if mid_raw and mid_raw != "\\N" else None
                            msg_ids = parts[col_map["msg_ids"]] if "msg_ids" in col_map and col_map["msg_ids"] < len(parts) and parts[col_map["msg_ids"]] != "\\N" else None
                            tg_fid = parts[col_map["tg_file_id"]] if "tg_file_id" in col_map and col_map["tg_file_id"] < len(parts) and parts[col_map["tg_file_id"]] != "\\N" else None
                            trash_raw = parts[col_map["is_trash"]] if "is_trash" in col_map and col_map["is_trash"] < len(parts) else "f"
                            is_trash = 1 if trash_raw in ("t", "1", "true", "TRUE") else 0
                            star_raw = parts[col_map["is_starred"]] if "is_starred" in col_map and col_map["is_starred"] < len(parts) else "f"
                            is_star = 1 if star_raw in ("t", "1", "true", "TRUE") else 0
                            pend_raw = parts[col_map["pending_deletion"]] if "pending_deletion" in col_map and col_map["pending_deletion"] < len(parts) else "f"
                            pending = 1 if pend_raw in ("t", "1", "true", "TRUE") else 0
                            enc_raw = parts[col_map["is_encrypted"]] if "is_encrypted" in col_map and col_map["is_encrypted"] < len(parts) else "f"
                            is_enc = 1 if enc_raw in ("t", "1", "true", "TRUE") else 0
                            salt = parts[col_map["encryption_salt"]] if "encryption_salt" in col_map and col_map["encryption_salt"] < len(parts) and parts[col_map["encryption_salt"]] != "\\N" else None
                            header = parts[col_map["encryption_header"]] if "encryption_header" in col_map and col_map["encryption_header"] < len(parts) and parts[col_map["encryption_header"]] != "\\N" else None
                            note = parts[col_map["note"]] if "note" in col_map and col_map["note"] < len(parts) and parts[col_map["note"]] != "\\N" else None
                            icon = parts[col_map["custom_icon"]] if "custom_icon" in col_map and col_map["custom_icon"] < len(parts) and parts[col_map["custom_icon"]] != "\\N" else None
                            ac_raw = parts[col_map["account_map"]] if "account_map" in col_map and col_map["account_map"] < len(parts) and parts[col_map["account_map"]] != "\\N" else "{}"
                            cat = parts[col_map["created_at"]] if "created_at" in col_map and col_map["created_at"] < len(parts) and parts[col_map["created_at"]] != "\\N" else ""
                            uat = parts[col_map["updated_at"]] if "updated_at" in col_map and col_map["updated_at"] < len(parts) and parts[col_map["updated_at"]] != "\\N" else ""
                            vfile_batch.append((vid, name[:1000], folder_id, size, mime, tg_mid, msg_ids, tg_fid, is_trash, is_star, pending, is_enc, salt, header, note, icon, ac_raw, cat, uat))
                            if not is_trash and not pending:
                                total_files_count += 1
                            if len(vfile_batch) >= 20000:
                                flush_batches()
                elif in_copy_table == "tg_accounts":
                    aid_raw = parts[col_map["id"]] if "id" in col_map and col_map["id"] < len(parts) else None
                    if aid_raw and aid_raw != "\\N":
                        try:
                            aid = int(aid_raw)
                            aname = parts[col_map["name"]] if "name" in col_map and col_map["name"] < len(parts) and parts[col_map["name"]] != "\\N" else f"Account {aid}"
                            account_batch.append((aid, aname))
                        except ValueError:
                            pass
                elif in_copy_table == "folder_closures":
                    anc_raw = parts[col_map["ancestor_id"]] if "ancestor_id" in col_map and col_map["ancestor_id"] < len(parts) else None
                    desc_raw = parts[col_map["descendant_id"]] if "descendant_id" in col_map and col_map["descendant_id"] < len(parts) else None
                    dp_raw = parts[col_map["depth"]] if "depth" in col_map and col_map["depth"] < len(parts) else None
                    if anc_raw and desc_raw and dp_raw and anc_raw != "\\N" and desc_raw != "\\N" and dp_raw != "\\N":
                        try:
                            closure_batch.append((int(anc_raw), int(desc_raw), int(dp_raw)))
                        except ValueError:
                            pass
                continue

            upper_line = line.upper()
            if upper_line.startswith("COPY "):
                parsed = _parse_copy_header(line)
                if not parsed:
                    continue
                table, columns = parsed
                if table in ("folders", "vfiles", "tg_accounts", "folder_closures"):
                    in_copy_table = table
                    col_map = {c: i for i, c in enumerate(columns)}
            elif upper_line.startswith("INSERT INTO "):
                m = _INSERT_RE.match(line)
                if not m:
                    continue
                table = m.group(1).lower()
                if table not in ("folders", "vfiles", "tg_accounts", "folder_closures"):
                    continue
                col_spec = [c.strip().strip('"').strip("'").lower() for c in m.group(2).split(",")] if m.group(2) else None
                vals_raw = m.group(3).rstrip(";")
                tokens = _parse_sql_tokens(vals_raw)
                if not tokens:
                    continue

                if table == "folders":
                    cols = col_spec or _DEFAULT_FOLDER_COLS
                    data = dict(zip(cols, tokens))
                    fid = _int_or_none(data.get("id"))
                    name = _text_value(data.get("name"), 1000)
                    if fid is not None and name:
                        trash_val = 1 if _bool_value(data.get("is_trash")) else 0
                        folder_batch.append((
                            fid,
                            name,
                            _int_or_none(data.get("parent_id")),
                            trash_val,
                            1 if _bool_value(data.get("is_starred")) else 0,
                            _text_value(data.get("color"), 32),
                            _text_value(data.get("note"), 5000),
                            _text_value(data.get("custom_icon"), 1000),
                            str(data.get("created_at") or ""),
                            str(data.get("updated_at") or ""),
                        ))
                        if not trash_val:
                            total_folders_count += 1
                        if len(folder_batch) >= 10000:
                            flush_batches()
                elif table == "vfiles":
                    cols = col_spec or _DEFAULT_VFILE_COLS
                    data = dict(zip(cols, tokens))
                    vid = _int_or_none(data.get("id"))
                    name = _text_value(data.get("name"), 1000)
                    if vid is not None and name:
                        mime = _text_value(data.get("mime_type"), 255) or mimetypes.guess_type(name)[0]
                        acc_map = _account_map(data.get("account_map"))
                        trash_val = 1 if _bool_value(data.get("is_trash")) else 0
                        pend_val = 1 if _bool_value(data.get("pending_deletion")) else 0
                        vfile_batch.append((
                            vid,
                            name,
                            _int_or_none(data.get("folder_id")),
                            _int_or_none(data.get("size")),
                            mime,
                            _int_or_none(data.get("tg_message_id")),
                            _text_value(data.get("msg_ids"), 2 * 1024 * 1024),
                            _text_value(data.get("tg_file_id"), 2 * 1024 * 1024),
                            trash_val,
                            1 if _bool_value(data.get("is_starred")) else 0,
                            pend_val,
                            1 if _bool_value(data.get("is_encrypted")) else 0,
                            _text_value(data.get("encryption_salt"), 2 * 1024 * 1024),
                            _text_value(data.get("encryption_header"), 2 * 1024 * 1024),
                            _text_value(data.get("note"), 5000),
                            _text_value(data.get("custom_icon"), 1000),
                            json.dumps(acc_map) if acc_map else "{}",
                            str(data.get("created_at") or ""),
                            str(data.get("updated_at") or ""),
                        ))
                        if not trash_val and not pend_val:
                            total_files_count += 1
                        if len(vfile_batch) >= 10000:
                            flush_batches()
                elif table == "tg_accounts":
                    cols = col_spec or ["id", "name"]
                    data = dict(zip(cols, tokens))
                    aid = _int_or_none(data.get("id"))
                    if aid is not None:
                        account_batch.append((aid, _text_value(data.get("name"), 300) or f"Account {aid}"))
                elif table == "folder_closures":
                    cols = col_spec or ["ancestor_id", "descendant_id", "depth"]
                    data = dict(zip(cols, tokens))
                    anc = _int_or_none(data.get("ancestor_id"))
                    desc = _int_or_none(data.get("descendant_id"))
                    dp = _int_or_none(data.get("depth"))
                    if anc is not None and desc is not None and dp is not None:
                        closure_batch.append((anc, desc, dp))

    flush_batches()

    # Index AFTER bulk inserts for 10x faster performance
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stg_folders_parent ON folders(parent_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stg_vfiles_folder ON vfiles(folder_id);")
    conn.commit()

    if total_folders_count == 0:
        total_folders_count = conn.execute("SELECT COUNT(*) FROM folders WHERE (is_trash IS NULL OR is_trash = 0)").fetchone()[0]
    if total_files_count == 0:
        total_files_count = conn.execute("SELECT COUNT(*) FROM vfiles WHERE (is_trash IS NULL OR is_trash = 0) AND (pending_deletion IS NULL OR pending_deletion = 0)").fetchone()[0]

    return total_folders_count, total_files_count


def stage_vfs_upload(upload: UploadFile, actor_id: int) -> Dict[str, Any]:
    _cleanup_sessions()
    root = _staging_root()
    import_id = uuid.uuid4().hex
    display_name = _safe_name(upload.filename)

    # Save uploaded file
    is_zip = display_name.lower().endswith(".zip")
    sql_path = root / f"{import_id}.sql"
    sqlite_path = root / f"{import_id}_stage.db"
    upload_path: Optional[Path] = None  # zip branch only; the except-cleanup below must not NameError

    total_bytes = 0
    try:
        upload.file.seek(0)
        if is_zip:
            upload_path = root / f"{import_id}_upload.zip"
            with upload_path.open("xb") as out:
                while chunk := upload.file.read(1024 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_IMPORT_BYTES:
                        raise VfsImportError("Файл превышает допустимый размер 256 МБ", 413)
                    out.write(chunk)
            if total_bytes == 0:
                raise VfsImportError("Загруженный файл пуст")
            with zipfile.ZipFile(upload_path, "r") as zf:
                sql_names = [n for n in zf.namelist() if n.lower().endswith((".sql", ".dump")) and not n.startswith("__MACOSX")]
                if not sql_names:
                    sql_names = [n for n in zf.namelist() if not n.endswith("/") and not n.startswith("__MACOSX")]
                if not sql_names:
                    raise VfsImportError("В ZIP-архиве не найден файл SQL-дампа")
                chosen = sql_names[0]
                with zf.open(chosen) as src, sql_path.open("wb") as dst:
                    decomp_bytes = 0
                    while c := src.read(1024 * 1024):
                        decomp_bytes += len(c)
                        if decomp_bytes > MAX_IMPORT_DECOMPRESSED_BYTES:
                            raise VfsImportError("Распакованный дамп превышает допустимый размер 1 ГБ", 413)
                        dst.write(c)
        else:
            with sql_path.open("xb") as out:
                while chunk := upload.file.read(1024 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_IMPORT_BYTES:
                        raise VfsImportError("Файл превышает допустимый размер 256 МБ", 413)
                    out.write(chunk)
            if total_bytes == 0:
                raise VfsImportError("Загруженный файл пуст")

        # Populate SQLite staging DB
        stage_conn = _init_staging_sqlite(sqlite_path)
        total_folders, total_files = _populate_staging_db(sql_path, stage_conn)

        # Query root folders
        root_folders_cur = stage_conn.execute("""
            SELECT f.id, f.name, f.parent_id,
                   (SELECT COUNT(*) FROM folders sf WHERE sf.parent_id = f.id AND (sf.is_trash IS NULL OR sf.is_trash = 0)) AS subfolder_count,
                   (SELECT COUNT(*) FROM vfiles sv WHERE sv.folder_id = f.id AND (sv.is_trash IS NULL OR sv.is_trash = 0) AND (sv.pending_deletion IS NULL OR sv.pending_deletion = 0)) AS file_count
            FROM folders f
            WHERE (f.parent_id IS NULL OR f.parent_id = 0)
              AND (f.is_trash IS NULL OR f.is_trash = 0)
            ORDER BY f.name COLLATE NOCASE
        """).fetchall()

        root_folders = [
            {
                "id": r["id"],
                "name": r["name"],
                "parent_id": r["parent_id"],
                "subfolder_count": r["subfolder_count"],
                "file_count": r["file_count"],
            }
            for r in root_folders_cur
        ]

        # Query root files
        root_files_cur = stage_conn.execute("""
            SELECT id, name, folder_id, size, mime_type, is_encrypted
            FROM vfiles
            WHERE (folder_id IS NULL OR folder_id = 0)
              AND (is_trash IS NULL OR is_trash = 0) AND (pending_deletion IS NULL OR pending_deletion = 0)
            ORDER BY name COLLATE NOCASE
        """).fetchall()

        root_files = [
            {
                "id": r["id"],
                "name": r["name"],
                "folder_id": r["folder_id"],
                "size": r["size"],
                "mime_type": r["mime_type"],
                "is_audio": _is_audio(r["name"], r["mime_type"]),
                "is_encrypted": bool(r["is_encrypted"]),
            }
            for r in root_files_cur
        ]

        stage_conn.close()

        # Save session
        state = ImportSession(
            import_id=import_id,
            path=sql_path,
            sqlite_path=sqlite_path,
            display_name=display_name,
            size_bytes=total_bytes,
            digest="",
            actor_id=actor_id,
            created_at=time.time(),
            status="ready",
        )
        with _SESSION_LOCK:
            _SESSIONS[import_id] = state

        return {
            "ok": True,
            "import_id": import_id,
            "filename": display_name,
            "size_bytes": total_bytes,
            "total_folders": total_folders,
            "total_files": total_files,
            "root_folders": root_folders,
            "root_files": root_files,
        }
    except Exception:
        _remove_file(upload_path)
        _remove_file(sql_path)
        _remove_file(sqlite_path)
        raise


def get_staged_children(import_id: str, actor_id: int, parent_id: Optional[int]) -> Dict[str, Any]:
    state = _get_session(import_id, actor_id)
    if not state.sqlite_path or not state.sqlite_path.is_file():
        raise VfsImportError("Сессия импорта не найдена или истёкла", 404)

    conn = sqlite3.connect(str(state.sqlite_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        if parent_id is None:
            folder_clause = "(parent_id IS NULL OR parent_id = 0 OR parent_id NOT IN (SELECT id FROM folders))"
            file_clause = "(folder_id IS NULL OR folder_id = 0 OR folder_id NOT IN (SELECT id FROM folders))"
            params: Tuple = ()
        else:
            folder_clause = "parent_id = ?"
            file_clause = "folder_id = ?"
            params = (parent_id,)

        subfolders_cur = conn.execute(f"""
            SELECT f.id, f.name, f.parent_id,
                   (SELECT COUNT(*) FROM folders sf WHERE sf.parent_id = f.id AND sf.is_trash = 0) AS subfolder_count,
                   (SELECT COUNT(*) FROM vfiles sv WHERE sv.folder_id = f.id AND sv.is_trash = 0 AND sv.pending_deletion = 0) AS file_count
            FROM folders f
            WHERE {folder_clause} AND f.is_trash = 0
            ORDER BY f.name COLLATE NOCASE
        """, params).fetchall()

        folders = [
            {
                "id": r["id"],
                "name": r["name"],
                "parent_id": r["parent_id"],
                "subfolder_count": r["subfolder_count"],
                "file_count": r["file_count"],
            }
            for r in subfolders_cur
        ]

        files_cur = conn.execute(f"""
            SELECT id, name, folder_id, size, mime_type, is_encrypted
            FROM vfiles
            WHERE {file_clause} AND is_trash = 0 AND pending_deletion = 0
            ORDER BY name COLLATE NOCASE
        """, params).fetchall()

        files = [
            {
                "id": r["id"],
                "name": r["name"],
                "folder_id": r["folder_id"],
                "size": r["size"],
                "mime_type": r["mime_type"],
                "is_audio": _is_audio(r["name"], r["mime_type"]),
                "is_encrypted": bool(r["is_encrypted"]),
            }
            for r in files_cur
        ]

        return {
            "ok": True,
            "parent_id": parent_id,
            "folders": folders,
            "files": files,
        }
    finally:
        conn.close()


def _expand_import_folders(
    conn: sqlite3.Connection,
    selected_folder_ids: List[int],
    preserve_parent_path: bool = True,
) -> Tuple[Set[int], Set[int], Dict[int, Dict[str, Any]]]:
    """
    Intelligently expands selected folder IDs:
    1. Distinguishes ancestor containers from leaf content roots.
       If folder A is an ancestor of folder B, and both A and B are in selected_folder_ids,
       A is an ancestor container (its unselected subfolders must NOT be expanded).
       Only leaf folders (folders with no selected descendants) are expanded recursively.
    2. If preserve_parent_path is True, non-root ancestor folders (e.g. 'Пранки' under 'Drive K:')
       of selected leaf folders are added as ancestor containers (so folder hierarchy is preserved),
       without expanding other sibling folders.
    Returns:
       all_folder_ids: set of all folder IDs (containers + content folders).
       content_folder_ids: set of folder IDs whose files are to be imported.
       staged_folders: dict of folder details from sqlite.
    """
    sel_set = set(selected_folder_ids)
    if not sel_set:
        return set(), set(), {}

    ancestor_folders: Set[int] = set()
    for fid in sel_set:
        descendants = conn.execute("""
            WITH RECURSIVE sub(id) AS (
                SELECT id FROM folders WHERE parent_id = ? AND is_trash = 0
                UNION ALL
                SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id WHERE f.is_trash = 0
            )
            SELECT id FROM sub
        """, (fid,)).fetchall()
        desc_ids = {r[0] for r in descendants}
        if desc_ids & sel_set:
            ancestor_folders.add(fid)

    leaf_selected_folders = sel_set - ancestor_folders
    content_folder_ids: Set[int] = set(leaf_selected_folders)

    for fid in leaf_selected_folders:
        descendants = conn.execute("""
            WITH RECURSIVE sub(id) AS (
                SELECT id FROM folders WHERE parent_id = ? AND is_trash = 0
                UNION ALL
                SELECT f.id FROM folders f JOIN sub ON f.parent_id = sub.id WHERE f.is_trash = 0
            )
            SELECT id FROM sub
        """, (fid,)).fetchall()
        for r in descendants:
            content_folder_ids.add(r[0])

    container_folder_ids: Set[int] = set(ancestor_folders)

    if preserve_parent_path:
        for fid in leaf_selected_folders:
            curr = fid
            seen: Set[int] = set()
            while True:
                if curr in seen:
                    # Cyclic parent chain in a corrupt/forged source dump: stop
                    # instead of looping forever and hanging the import worker.
                    break
                seen.add(curr)
                row = conn.execute("SELECT id, name, parent_id FROM folders WHERE id = ?", (curr,)).fetchone()
                if not row or not row["parent_id"]:
                    break
                pid = row["parent_id"]
                p_row = conn.execute("SELECT id, name, parent_id FROM folders WHERE id = ?", (pid,)).fetchone()
                if not p_row:
                    break
                p_name = p_row["name"] or ""
                is_drive = bool(re.match(r"^(drive\s*[a-z]:?|[a-z]:\\?)$", p_name.strip(), re.IGNORECASE))
                is_backup_root = (p_row["parent_id"] is None or p_row["parent_id"] == 0)
                if is_drive and is_backup_root:
                    break
                container_folder_ids.add(pid)
                if is_backup_root:
                    break
                curr = pid

    all_folder_ids = content_folder_ids | container_folder_ids

    staged_folders: Dict[int, Dict[str, Any]] = {}
    if all_folder_ids:
        placeholders = ",".join("?" for _ in all_folder_ids)
        f_cur = conn.execute(
            f"""SELECT id, name, parent_id, is_starred, color, note, custom_icon, created_at, updated_at
                FROM folders WHERE id IN ({placeholders}) AND is_trash = 0""",
            list(all_folder_ids),
        ).fetchall()
        for r in f_cur:
            staged_folders[r["id"]] = dict(r)

    return all_folder_ids, content_folder_ids, staged_folders


def _sync_catalog_for_imported_tree(
    db: Session,
    folder_mapping: Dict[int, int],
    staged_folders: Dict[int, Dict[str, Any]],
    target_root_id: Optional[int],
    catalog_kind: str = "compilation",
) -> None:
    if catalog_kind == "none":
        return

    try:
        from sqlalchemy import inspect
        conn = db.connection()
        if conn is not None:
            inspector = inspect(conn)
            if not inspector.has_table("music_artists") or not inspector.has_table("music_albums"):
                return
    except Exception:
        logger.debug("Catalog table inspection failed; skipping catalog sync", exc_info=True)
        return

    top_level_target_ids = []
    for staged_id, target_id in folder_mapping.items():
        if target_id is None:
            continue
        staged_folder = staged_folders.get(staged_id)
        if not staged_folder:
            continue

        target_folder = FolderRepository.get(db, target_id)
        if not target_folder or target_folder.is_trash:
            continue

        staged_parent_id = staged_folder.get("parent_id")
        mapped_parent_id = folder_mapping.get(staged_parent_id) if staged_parent_id else None

        is_top_level = (mapped_parent_id is None) or (target_folder.parent_id == target_root_id)
        if is_top_level:
            top_level_target_ids.append(target_folder.id)

    from app.services.music_library_service import CatalogAdminService
    for tf_id in top_level_target_ids:
        try:
            CatalogAdminService.sync_catalog_folder_tree(db, tf_id, catalog_kind=catalog_kind)
        except Exception:
            logger.warning("Recursive catalog sync failed for folder %s", tf_id, exc_info=True)

    db.flush()


def preview_tree_import(
    db: Session,
    import_id: str,
    actor_id: int,
    selected_folder_ids: List[int],
    selected_file_ids: List[int],
    target_root_id: Optional[int],
    preserve_parent_path: bool = True,
    catalog_kind: str = "compilation",
) -> Dict[str, Any]:
    state = _get_session(import_id, actor_id)
    if not state.sqlite_path or not state.sqlite_path.is_file():
        raise VfsImportError("Сессия импорта не найдена или истёкла", 404)

    conn = sqlite3.connect(str(state.sqlite_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        all_folder_ids, content_folder_ids, staged_folders = _expand_import_folders(
            conn, selected_folder_ids, preserve_parent_path=preserve_parent_path
        )

        folder_mapping: Dict[int, Optional[int]] = {}
        folder_actions: Dict[int, str] = {}
        preview_items: List[Dict[str, Any]] = []

        target_index = _target_index(db)
        target_root = _target_root(db, target_root_id)

        # Memoized depth (O(n) total) instead of recomputing the whole parent
        # chain for every sort key (was O(n * depth) -> O(n^2) on a deep tree).
        depth_cache: Dict[int, int] = {}

        def get_staged_depth(fid: int) -> int:
            cached = depth_cache.get(fid)
            if cached is not None:
                return cached
            path: List[int] = []
            seen: Set[int] = set()
            cur: Optional[int] = fid
            while cur is not None and cur in staged_folders and cur not in depth_cache and cur not in seen:
                seen.add(cur)
                path.append(cur)
                parent = staged_folders[cur]["parent_id"]
                cur = parent if (parent is not None and parent in staged_folders) else None
            base = depth_cache[cur] if (cur is not None and cur in depth_cache) else 0
            for node_id in reversed(path):
                base += 1
                depth_cache[node_id] = base
            return depth_cache[fid]

        ordered_folder_ids = sorted(all_folder_ids, key=get_staged_depth)

        for fid in ordered_folder_ids:
            f = staged_folders[fid]
            parent_in_selection = f["parent_id"] in staged_folders
            if not parent_in_selection:
                if target_root_id is None:
                    candidates = target_index.get((None, f["name"].casefold()), [])
                    if candidates:
                        folder_mapping[fid] = candidates[0].id
                        folder_actions[fid] = "reuse"
                    else:
                        folder_mapping[fid] = None
                        folder_actions[fid] = "create"
                else:
                    if target_root and target_root.name.casefold() == f["name"].casefold():
                        folder_mapping[fid] = target_root.id
                        folder_actions[fid] = "reuse"
                    else:
                        candidates = target_index.get((target_root_id, f["name"].casefold()), [])
                        if candidates:
                            folder_mapping[fid] = candidates[0].id
                            folder_actions[fid] = "reuse"
                        else:
                            folder_mapping[fid] = None
                            folder_actions[fid] = "create"
            else:
                mapped_parent_id = folder_mapping.get(f["parent_id"])
                if mapped_parent_id is not None:
                    candidates = target_index.get((mapped_parent_id, f["name"].casefold()), [])
                    if candidates:
                        folder_mapping[fid] = candidates[0].id
                        folder_actions[fid] = "reuse"
                    else:
                        folder_mapping[fid] = None
                        folder_actions[fid] = "create"
                else:
                    folder_mapping[fid] = None
                    folder_actions[fid] = "create"

            preview_items.append({
                "kind": "folder",
                "id": fid,
                "name": f["name"],
                "action": folder_actions[fid],
            })

        # Fetch files to import from content folders only
        file_rows: List[sqlite3.Row] = []
        if content_folder_ids:
            placeholders = ",".join("?" for _ in content_folder_ids)
            f_cur = conn.execute(
                f"SELECT id, name, folder_id, size, mime_type FROM vfiles WHERE folder_id IN ({placeholders}) AND is_trash = 0 AND pending_deletion = 0",
                list(content_folder_ids),
            ).fetchall()
            file_rows.extend(f_cur)

        # Include explicitly selected files if their folder was not selected
        extra_file_ids = set(selected_file_ids) - {r["id"] for r in file_rows}
        if extra_file_ids:
            placeholders = ",".join("?" for _ in extra_file_ids)
            f_cur = conn.execute(
                f"SELECT id, name, folder_id, size, mime_type FROM vfiles WHERE id IN ({placeholders}) AND is_trash = 0 AND pending_deletion = 0",
                list(extra_file_ids),
            ).fetchall()
            file_rows.extend(f_cur)

        # Preload existing file names in target T-Music folders to check duplicates
        target_mapped_ids = [fid for fid in folder_mapping.values() if fid is not None]
        if target_root_id is not None and target_root_id not in target_mapped_ids:
            target_mapped_ids.append(target_root_id)
        existing_file_names = _existing_names(db, target_mapped_ids)

        files_imported = 0
        files_skipped = 0
        seen_in_import: Set[Tuple[Any, str]] = set()

        for r in file_rows:
            fid = r["folder_id"]
            name = r["name"]
            mapped_target = folder_mapping.get(fid) if fid is not None else target_root_id
            name_lower = name.casefold()

            is_duplicate = False
            if mapped_target is not None:
                if name_lower in existing_file_names.get(mapped_target, set()):
                    is_duplicate = True
                elif (mapped_target, name_lower) in seen_in_import:
                    is_duplicate = True

            if is_duplicate:
                files_skipped += 1
                if len(preview_items) < PREVIEW_ITEM_LIMIT:
                    preview_items.append({
                        "kind": "file",
                        "id": r["id"],
                        "name": name,
                        "action": "skip",
                        "reason": "already_exists",
                        "size": r["size"],
                    })
            else:
                files_imported += 1
                if mapped_target is not None:
                    seen_in_import.add((mapped_target, name_lower))
                if len(preview_items) < PREVIEW_ITEM_LIMIT:
                    preview_items.append({
                        "kind": "file",
                        "id": r["id"],
                        "name": name,
                        "action": "create",
                        "size": r["size"],
                    })

        folders_create = sum(1 for a in folder_actions.values() if a == "create")
        folders_reuse = sum(1 for a in folder_actions.values() if a == "reuse")

        summary = {
            "folders_total": len(all_folder_ids),
            "folders_create": folders_create,
            "folders_reuse": folders_reuse,
            "files_total": len(file_rows),
            "files_imported": files_imported,
            "files_skipped": files_skipped,
            "can_run": files_imported > 0 or folders_create > 0,
        }

        return {
            "ok": True,
            "import_id": import_id,
            "target_root_id": target_root_id,
            "summary": summary,
            "preview_items": preview_items,
        }
    finally:
        conn.close()


def run_tree_import(
    db: Session,
    import_id: str,
    actor_id: int,
    selected_folder_ids: List[int],
    selected_file_ids: List[int],
    target_root_id: Optional[int],
    confirm: bool,
    preserve_parent_path: bool = True,
    catalog_kind: str = "compilation",
    before_commit: Optional[Callable[[Session, Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    state = _get_session(import_id, actor_id)
    if not confirm:
        raise VfsImportError("Для запуска требуется подтверждение")
    if not state.sqlite_path or not state.sqlite_path.is_file():
        raise VfsImportError("Сессия импорта не найдена или истёкла", 404)

    if not _RUN_LOCK.acquire(blocking=False):
        raise VfsImportError("Другой VFS-импорт уже выполняется", 409)

    conn = None
    try:
        conn = sqlite3.connect(str(state.sqlite_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        with _SESSION_LOCK:
            state.status = "running"
            state.progress = {
                "current": 0,
                "total": 0,
                "folders_created": 0,
                "folders_reused": 0,
                "files_imported": 0,
                "files_skipped": 0,
                "stage": "preparing",
                "message": "Подготовка данных…",
            }

        all_folder_ids, content_folder_ids, staged_folders = _expand_import_folders(
            conn, selected_folder_ids, preserve_parent_path=preserve_parent_path
        )

        # Target index in current T-Music database
        target_index = _target_index(db)
        target_root = _target_root(db, target_root_id)

        # Memoized depth (O(n) total) instead of recomputing the whole parent
        # chain for every sort key (was O(n * depth) -> O(n^2) on a deep tree).
        depth_cache: Dict[int, int] = {}

        def get_staged_depth(fid: int) -> int:
            cached = depth_cache.get(fid)
            if cached is not None:
                return cached
            path: List[int] = []
            seen: Set[int] = set()
            cur: Optional[int] = fid
            while cur is not None and cur in staged_folders and cur not in depth_cache and cur not in seen:
                seen.add(cur)
                path.append(cur)
                parent = staged_folders[cur]["parent_id"]
                cur = parent if (parent is not None and parent in staged_folders) else None
            base = depth_cache[cur] if (cur is not None and cur in depth_cache) else 0
            for node_id in reversed(path):
                base += 1
                depth_cache[node_id] = base
            return depth_cache[fid]

        ordered_folder_ids = sorted(all_folder_ids, key=get_staged_depth)
        folder_mapping: Dict[int, int] = {}
        folders_created = 0
        folders_reused = 0

        state.progress["stage"] = "folders"
        state.progress["message"] = "Создание папок…"

        for fid in ordered_folder_ids:
            f = staged_folders[fid]
            parent_in_selection = f["parent_id"] in staged_folders
            if not parent_in_selection:
                if target_root_id is None:
                    candidates = target_index.get((None, f["name"].casefold()), [])
                    if candidates:
                        folder_mapping[fid] = candidates[0].id
                        folders_reused += 1
                    else:
                        new_folder = Folder(
                            name=f["name"],
                            parent_id=None,
                            is_trash=False,
                            is_starred=bool(f.get("is_starred")),
                            color=f.get("color"),
                            note=f.get("note"),
                            custom_icon=f.get("custom_icon"),
                            cached_total_size=0,
                            cached_file_count=0,
                        )
                        db.add(new_folder)
                        db.flush()
                        folder_mapping[fid] = new_folder.id
                        target_index.setdefault((None, f["name"].casefold()), []).append(new_folder)
                        folders_created += 1
                else:
                    if target_root and target_root.name.casefold() == f["name"].casefold():
                        folder_mapping[fid] = target_root.id
                        folders_reused += 1
                    else:
                        candidates = target_index.get((target_root_id, f["name"].casefold()), [])
                        if candidates:
                            folder_mapping[fid] = candidates[0].id
                            folders_reused += 1
                        else:
                            new_folder = Folder(
                                name=f["name"],
                                parent_id=target_root_id,
                                is_trash=False,
                                is_starred=bool(f.get("is_starred")),
                                color=f.get("color"),
                                note=f.get("note"),
                                custom_icon=f.get("custom_icon"),
                                cached_total_size=0,
                                cached_file_count=0,
                            )
                            db.add(new_folder)
                            db.flush()
                            folder_mapping[fid] = new_folder.id
                            target_index.setdefault((target_root_id, f["name"].casefold()), []).append(new_folder)
                            folders_created += 1
            else:
                parent_id = folder_mapping.get(f["parent_id"])
                candidates = target_index.get((parent_id, f["name"].casefold()), [])
                if candidates:
                    folder_mapping[fid] = candidates[0].id
                    folders_reused += 1
                else:
                    new_folder = Folder(
                        name=f["name"],
                        parent_id=parent_id,
                        is_trash=False,
                        is_starred=bool(f.get("is_starred")),
                        color=f.get("color"),
                        note=f.get("note"),
                        custom_icon=f.get("custom_icon"),
                        cached_total_size=0,
                        cached_file_count=0,
                    )
                    db.add(new_folder)
                    db.flush()
                    folder_mapping[fid] = new_folder.id
                    target_index.setdefault((parent_id, f["name"].casefold()), []).append(new_folder)
                    folders_created += 1

        state.progress["folders_created"] = folders_created
        state.progress["folders_reused"] = folders_reused

        # Rebuild closures
        _rebuild_closures(db)

        # Account mapping
        acc_cur = conn.execute("SELECT id, name FROM tg_accounts").fetchall()
        stage_snapshot = SourceSnapshot(
            account_ids={r["id"] for r in acc_cur},
            account_names={r["id"]: r["name"] for r in acc_cur if r["name"]},
        )
        account_mapping = _account_mapping(stage_snapshot, db)

        # Fetch files from content folders only (ancestor containers have no files imported)
        file_rows: List[sqlite3.Row] = []
        if content_folder_ids:
            placeholders = ",".join("?" for _ in content_folder_ids)
            f_cur = conn.execute(
                f"""SELECT id, name, folder_id, size, mime_type, tg_message_id, msg_ids, tg_file_id,
                           is_starred, is_trash, pending_deletion, is_encrypted, encryption_salt,
                           encryption_header, note, custom_icon, account_map, created_at, updated_at
                    FROM vfiles WHERE folder_id IN ({placeholders}) AND is_trash = 0 AND pending_deletion = 0""",
                list(content_folder_ids),
            ).fetchall()
            file_rows.extend(f_cur)

        extra_file_ids = set(selected_file_ids) - {r["id"] for r in file_rows}
        if extra_file_ids:
            placeholders = ",".join("?" for _ in extra_file_ids)
            f_cur = conn.execute(
                f"""SELECT id, name, folder_id, size, mime_type, tg_message_id, msg_ids, tg_file_id,
                           is_starred, is_trash, pending_deletion, is_encrypted, encryption_salt,
                           encryption_header, note, custom_icon, account_map, created_at, updated_at
                    FROM vfiles WHERE id IN ({placeholders}) AND is_trash = 0 AND pending_deletion = 0""",
                list(extra_file_ids),
            ).fetchall()
            file_rows.extend(f_cur)

        state.progress["total"] = len(file_rows)
        state.progress["stage"] = "files"
        state.progress["message"] = "Импорт треков…"

        target_mapped_ids = [fid for fid in folder_mapping.values() if fid is not None]
        if target_root_id is not None and target_root_id not in target_mapped_ids:
            target_mapped_ids.append(target_root_id)
        existing_file_names = _existing_names(db, target_mapped_ids)

        files_imported = 0
        files_skipped = 0
        seen_in_import: Set[Tuple[Any, str]] = set()
        batch: List[VFile] = []

        for r in file_rows:
            fid = r["folder_id"]
            name = r["name"]
            mapped_target = folder_mapping.get(fid) if fid is not None else target_root_id
            name_lower = name.casefold()

            is_duplicate = False
            if mapped_target is not None:
                if name_lower in existing_file_names.get(mapped_target, set()):
                    is_duplicate = True
                elif (mapped_target, name_lower) in seen_in_import:
                    is_duplicate = True

            if is_duplicate:
                files_skipped += 1
            else:
                if mapped_target is not None:
                    seen_in_import.add((mapped_target, name_lower))
                acc_map_parsed = _account_map(r["account_map"])
                acc_map_remapped = _remap_account_map(acc_map_parsed, account_mapping)
                raw_mime = r["mime_type"]
                if _is_audio(name, raw_mime):
                    if not (raw_mime and raw_mime.casefold().startswith("audio/")):
                        raw_mime = mimetypes.guess_type(name)[0] or "audio/mpeg"
                batch.append(
                    VFile(
                        name=name,
                        folder_id=mapped_target,
                        size=r["size"],
                        mime_type=raw_mime,
                        tg_message_id=r["tg_message_id"],
                        msg_ids=r["msg_ids"],
                        tg_file_id=r["tg_file_id"],
                        is_starred=bool(r["is_starred"]),
                        is_trash=False,
                        pending_deletion=False,
                        encryption_salt=r["encryption_salt"],
                        encryption_header=r["encryption_header"],
                        is_encrypted=bool(r["is_encrypted"]),
                        note=r["note"],
                        custom_icon=r["custom_icon"],
                        account_map=acc_map_remapped,
                    )
                )
                files_imported += 1

                if len(batch) >= 500:
                    db.add_all(batch)
                    db.flush()
                    batch.clear()

            state.progress["current"] = files_imported + files_skipped
            state.progress["files_imported"] = files_imported
            state.progress["files_skipped"] = files_skipped

        if batch:
            db.add_all(batch)
            db.flush()
            batch.clear()

        _rebuild_closures(db)
        _sync_catalog_for_imported_tree(
            db, folder_mapping, staged_folders, target_root_id, catalog_kind=catalog_kind
        )
        result = {
            "ok": True,
            "import_id": import_id,
            "status": "completed",
            "folders_created": folders_created,
            "folders_reused": folders_reused,
            "files_imported": files_imported,
            "files_skipped": files_skipped,
            "total_files": len(file_rows),
            "completed_at": utcnow().isoformat(),
        }

        if before_commit is not None:
            before_commit(db, result)
        db.commit()
        with _SESSION_LOCK:
            state.status = "completed"
            state.error = None
            state.result = result
            state.progress["stage"] = "completed"
            state.progress["message"] = "Импорт успешно завершён!"

        return result
    except VfsImportError as exc:
        db.rollback()
        with _SESSION_LOCK:
            state.status = "failed"
            state.error = str(exc)
            state.progress["stage"] = "failed"
            state.progress["message"] = str(exc)
        raise
    except Exception as exc:
        db.rollback()
        reference = uuid.uuid4().hex[:12]
        message = f"Импорт не выполнен. Код ошибки: {reference}."
        with _SESSION_LOCK:
            state.status = "failed"
            state.error = message
            state.progress["stage"] = "failed"
            state.progress["message"] = message
        logger.exception("VFS tree import failed; reference=%s", reference)
        raise VfsImportError(message, 500) from exc
    finally:
        try:
            if conn is not None:
                conn.close()
        finally:
            _RUN_LOCK.release()

