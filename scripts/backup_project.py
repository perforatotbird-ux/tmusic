#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T-Music — резервная копия исходников проекта в ZIP.

В архив попадает только то, что нужно для запуска, сборки и разработки:
код backend/frontend/mobile, тесты, скрипты, конфиги запуска, документация
Obsidian и корневые отчёты. Всё остальное отсекается по явным правилам.

Никогда не попадают в архив кода:
  * кеши и артефакты сборки — node_modules, __pycache__, .pytest_cache,
    .ruff_cache, frontend/dist, mobile/android/build, .gradle;
  * runtime-данные — data/, cache/, backend/storage, backend/data, логи;
  * тяжёлые бинарники ffmpeg (backend/app/utils/ffmpeg/*.dll|exe) — они
    скачиваются заново через `ensure_ffmpeg_binaries()`;
  * секреты — music.env, .secrets/, *.session. По умолчанию они не копируются
    вообще; `--with-secrets` кладёт их в ОТДЕЛЬНЫЙ архив, который нельзя
    смешивать с кодом и нельзя класть в git/облако без шифрования.

Запуск (из любого каталога):

    python scripts/backup_project.py                    # код в ZIP рядом с проектом
    python scripts/backup_project.py --list             # только показать состав
    python scripts/backup_project.py --keep 10          # хранить 10 последних архивов
    python scripts/backup_project.py --label before-upgrade
    python scripts/backup_project.py --with-secrets --with-git
    python scripts/backup_project.py --with-runtime --out-dir /mnt/backups

Зависимостей нет — только стандартная библиотека (Python 3.9+).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable, Iterator, Sequence

# ─── Что считается проектом ───────────────────────────────────────────────────

# Признаки корня проекта: без них скрипт откажется работать, чтобы случайный
# запуск из чужого каталога не создал бессмысленный архив.
PROJECT_MARKERS = ("docker-compose.yml", "backend/app/main.py")

# Явный список того, что бэкапим (allowlist, а не «всё минус исключения»):
# так в архив не просочится ничего лишнего, даже если в корне появится мусор.
INCLUDE_PATHS: tuple[str, ...] = (
    # корень: документация и конфигурация запуска
    "AGENTS.md",
    "README.md",
    "SECURITY.md",
    "overview.md",
    "docker-compose.yml",
    "Dockerfile",
    ".dockerignore",
    ".gitignore",
    "music.env.example",
    "start_tmusic.bat",
    "restart.bat",
    "*.html",              # отчёты аудитов и планы в корне
    # backend
    "backend/app",
    "backend/tests",
    "backend/requirements.txt",
    # frontend (исходники и тесты; dist пересобирается)
    "frontend/src",
    "frontend/public",
    "frontend/tests",
    "frontend/index.html",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/vite.config.js",
    # mobile: www-лончер + нативные исходники Android
    "mobile/www",
    "mobile/android",
    "mobile/capacitor.config.ts",
    "mobile/package.json",
    "mobile/package-lock.json",
    "mobile/README.md",
    # служебное и база знаний
    "scripts",
    "obsidian",
    ".sec_audit",
)

# Runtime-данные: не нужны для работы кода, но иногда нужны для восстановления
# (только через --with-runtime).
RUNTIME_PATHS: tuple[str, ...] = ("data", "cache", "frontend/dist")


# Каталоги-мусор на любом уровне вложенности.
EXCLUDE_DIR_NAMES = frozenset({
    "node_modules", "__pycache__", "__MACOSX",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
    ".venv", "venv", "env", "htmlcov",
    "dist", "build", ".gradle", ".kotlin",
    ".idea", ".vscode", ".mimocode", ".workbuddy-ai", ".freebuff",
    "storage",
})

# Скрытые каталоги, которые всё же являются частью проекта.
HIDDEN_DIR_ALLOW = frozenset({".sec_audit", ".github"})

# Файлы-мусор на любом уровне вложенности.
EXCLUDE_FILE_PATTERNS: tuple[str, ...] = (
    "*.pyc", "*.pyo", "*.pyd",
    "*.log", "*.tmp", "*.temp", "*.bak", "*.orig", "*.rej", "*.swp", "*~",
    ".DS_Store", "Thumbs.db", "desktop.ini",
    # незавершённые артефакты транскодинга и сессии Telegram
    "*.part.mp3", "*.session", "*.session-journal",
    )

# В режиме --with-runtime дампы и архивы нужны, поэтому эти шаблоны снимаются.
RUNTIME_PATTERN_EXCEPTIONS = frozenset({
    "*.dump", "*.sql", "*.sqlite", "*.sqlite3", "*.db", "*.zip", "*.tar", "*.tgz",
})

# Сгенерированные `npx cap sync` артефакты внутри Android-проекта.
EXCLUDE_PATH_PREFIXES: tuple[str, ...] = (
    "mobile/android/app/src/main/assets/",
)

# Минимальный набор, без которого восстановленный проект не запустится.
# Проверяется после записи архива — молчаливый «пустой» бэкап недопустим.
CRITICAL_FILES: tuple[str, ...] = (
    "docker-compose.yml",
    "backend/requirements.txt",
    "backend/app/main.py",
    "frontend/package.json",
    "frontend/src/main.jsx",
    "mobile/capacitor.config.ts",
)

# Разумный предел для одного файла: всё, что больше, почти наверняка мусор.
LARGE_FILE_WARN_BYTES = 20 * 1024 * 1024


# ─── Сбор файлов ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Entry:
    rel: str
    size: int


def _matches_any(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch(name, pattern) for pattern in patterns)


def _skip_dir(name: str, *, with_git: bool, excluded: frozenset[str]) -> bool:
    if name == ".git":
        return not with_git
    if name in excluded:
        return True
    if name.startswith("."):
        return name not in HIDDEN_DIR_ALLOW
    return False


def _walk_tree(
    root: Path,
    rel_dir: str,
    *,
    with_git: bool,
    excluded_dirs: frozenset[str],
    file_patterns: Sequence[str],
) -> Iterator[Entry]:
    """Обойти каталог, отсекая мусор на входе (не спускаемся в node_modules и т.п.)."""
    for dirpath, dirnames, filenames in os.walk(root / rel_dir):
        here = Path(dirpath)
        dirnames[:] = sorted(
            name for name in dirnames
            if not _skip_dir(name, with_git=with_git, excluded=excluded_dirs)
            and not (here / name).is_symlink()
        )
        for name in sorted(filenames):
            path = here / name
            if path.is_symlink() or _matches_any(name, file_patterns):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            yield Entry(path.relative_to(root).as_posix(), size)


def collect_entries(
    root: Path,
    *,
    include_paths: Sequence[str] = INCLUDE_PATHS,
    with_git: bool = False,
    with_runtime: bool = False,
) -> tuple[list[Entry], list[str]]:
    """Вернуть (файлы, заметки) для архива. Ничего не пишет на диск."""
    excluded_dirs = EXCLUDE_DIR_NAMES
    file_patterns: Sequence[str] = EXCLUDE_FILE_PATTERNS
    if with_runtime:
        # runtime-данные включают дампы БД, кеш обложек и собранный dist
        excluded_dirs = EXCLUDE_DIR_NAMES - {"dist"}
        file_patterns = tuple(
            pattern for pattern in EXCLUDE_FILE_PATTERNS
            if pattern not in RUNTIME_PATTERN_EXCEPTIONS
        )

    wanted = list(include_paths) + (list(RUNTIME_PATHS) if with_runtime else [])
    if with_git:
        wanted.append(".git")

    entries: list[Entry] = []
    notes: list[str] = []
    for rel in wanted:
        # Шаблоны вида `*.html` раскрываются по верхнему уровню проекта.
        if any(char in rel for char in "*?["):
            matches = sorted(root.glob(rel))
            if not matches:
                notes.append(f"нет совпадений: {rel}")
            for match in matches:
                if match.is_file() and not _matches_any(match.name, file_patterns):
                    entries.append(Entry(match.relative_to(root).as_posix(), match.stat().st_size))
            continue

        target = root / rel
        if not target.exists():
            notes.append(f"нет пути: {rel}")
            continue
        if target.is_file():
            entries.append(Entry(rel, target.stat().st_size))
            continue
        entries.extend(_walk_tree(
            root, rel,
            with_git=with_git,
            excluded_dirs=excluded_dirs,
            file_patterns=file_patterns,
        ))

    # Префиксные исключения (сгенерированные assets внутри Android-проекта).
    entries = [
        entry for entry in entries
        if not any(entry.rel.startswith(prefix) for prefix in EXCLUDE_PATH_PREFIXES)
    ]

    unique: dict[str, Entry] = {entry.rel: entry for entry in entries}
    return sorted(unique.values(), key=lambda entry: entry.rel), notes


# ─── Вспомогательное ──────────────────────────────────────────────────────────

def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            return f"{value:.1f} {unit}" if unit != "Б" else f"{int(value)} Б"
        value /= 1024
    return f"{value:.1f} ТБ"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head(root: Path) -> str:
    """Короткий хеш HEAD, если git доступен. Никогда не роняет бэкап."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "n/a"
    head = result.stdout.strip()
    return head if result.returncode == 0 and head else "n/a"


def git_dirty(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(result.stdout.strip())


def safe_label(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")


def find_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parent.parent
    missing = [marker for marker in PROJECT_MARKERS if not (root / marker).exists()]
    if missing:
        raise SystemExit(
            f"[!] {root} не похож на корень T-Music: нет {', '.join(missing)}.\n"
            "    Укажите корень явно: --root <путь>"
        )
    return root


# ─── Запись архива ────────────────────────────────────────────────────────────

def write_zip(archive: Path, root: Path, entries: Sequence[Entry]) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for entry in entries:
            zf.write(root / entry.rel, arcname=entry.rel)


def verify_zip(archive: Path) -> tuple[int, list[str]]:
    """Проверить, что архив читается и содержит критичные файлы."""
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise SystemExit(f"[!] Архив повреждён: {bad}")
        names = set(zf.namelist())
    missing = [name for name in CRITICAL_FILES if name not in names]
    return len(names), missing


def write_manifest(
    path: Path,
    *,
    root: Path,
    archive: Path,
    entries: Sequence[Entry],
    notes: Sequence[str],
    with_git: bool,
    with_runtime: bool,
    secrets_archive: Path | None,
) -> None:
    total = sum(entry.size for entry in entries)
    lines = [
        "T-Music — резервная копия исходников",
        f"Создан:        {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Корень:        {root}",
        f"Git HEAD:      {git_head(root)}" + (" (есть незакоммиченные изменения)" if git_dirty(root) else ""),
        f"Архив:         {archive.name}",
        f"SHA-256:       {sha256_of(archive)}",
        f"Файлов:        {len(entries)}",
        f"Размер данных: {human_bytes(total)}",
        f"Размер архива: {human_bytes(archive.stat().st_size)}",
        f"Опции:         git={with_git} runtime={with_runtime}",
        "",
        "Состав (allowlist):",
        *[f"  - {item}" for item in INCLUDE_PATHS],
        *([f"  - {item}   [--with-runtime]" for item in RUNTIME_PATHS] if with_runtime else []),
        *([f"  - {item}   [--with-git]" for item in (".git",)] if with_git else []),
        "",
        "Исключено всегда:",
        "  - каталоги: " + ", ".join(sorted(EXCLUDE_DIR_NAMES)),
        "  - файлы:    " + ", ".join(EXCLUDE_FILE_PATTERNS),
        "  - префиксы: " + ", ".join(EXCLUDE_PATH_PREFIXES),
        "",
        "Секреты: music.env и .secrets/ в архив кода не входят.",
    ]
    if secrets_archive is not None:
        lines.append(f"  ! секреты лежат отдельно: {secrets_archive.name} (не кладите в git/облако без шифрования)")
    else:
        lines.append("  ! секреты не сохранялись (--with-secrets, если нужен отдельный архив)")
    if notes:
        lines += ["", "Замечания:", *[f"  - {note}" for note in notes]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prune_old(out_dir: Path, project_name: str, keep: int) -> list[Path]:
    """Удалить старые архивы ЭТОГО проекта, оставив последние `keep`.

    Архивы кода и архивы секретов считаются отдельно, чтобы `--keep 3` не
    оставлял «сироту» без парного файла. Удаляются только файлы, чьё имя
    совпадает с шаблоном бэкапа: чужие zip-файлы в каталоге не трогаются.
    """
    if keep <= 0:
        return []
    name_re = re.compile(rf"^{re.escape(project_name)}-\d{{8}}-\d{{6}}.*\.zip$")
    groups: dict[bool, list[Path]] = {True: [], False: []}  # is_secrets -> файлы
    for path in out_dir.iterdir():
        if path.is_file() and name_re.match(path.name):
            groups[path.name.endswith(".secrets.zip")].append(path)

    removed: list[Path] = []
    for files in groups.values():
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for path in files[keep:]:
            for companion in (path, path.with_suffix(".manifest.txt")):
                try:
                    if companion.exists():
                        companion.unlink()
                        if companion == path:
                            removed.append(path)
                except OSError as error:
                    print(f"[!] не удалось удалить {companion.name}: {error}")
    return removed


# ─── CLI ──────────────────────────────────────────────────────────────────────

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backup_project.py",
        description="Резервная копия исходников T-Music в ZIP (только нужные файлы).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python scripts/backup_project.py --list\n"
            "  python scripts/backup_project.py --out-dir D:\\Backups --keep 10\n"
            "  python scripts/backup_project.py --with-secrets --with-git\n"
        ),
    )
    parser.add_argument("--root", default=None, help="корень проекта (по умолчанию — каталог скрипта/..)")
    parser.add_argument("--out-dir", default=None, help="куда положить архив (по умолчанию — рядом с проектом)")
    parser.add_argument("--label", default="", help="метка в имени файла, например before-upgrade")
    parser.add_argument("--keep", type=int, default=0, help="сколько последних архивов оставить (0 = не удалять)")
    parser.add_argument("--list", action="store_true", help="только показать состав, ничего не создавать")
    parser.add_argument("--with-git", action="store_true", help="включить историю .git")
    parser.add_argument("--with-secrets", action="store_true", help="отдельный архив с music.env и .secrets/")
    parser.add_argument("--with-runtime", action="store_true", help="включить data/, cache/ и frontend/dist")
    parser.add_argument("--quiet", action="store_true", help="минимум вывода (для планировщика)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    log = (lambda *parts: None) if args.quiet else print

    root = find_root(args.root)
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else root.parent / f"{root.name}-backups"
    project_name = safe_label(root.name) or "project"

    entries, notes = collect_entries(root, with_git=args.with_git, with_runtime=args.with_runtime)
    total = sum(entry.size for entry in entries)

    log(f"[i] Проект:  {root}")
    log(f"[i] Состав:  {len(entries)} файлов, {human_bytes(total)} (без сжатия)")
    if args.with_runtime:
        log("[!] --with-runtime: в архив попадут data/ (дампы БД с хэшами паролей) и cache/")
    if args.with_git:
        log("[i] История .git включена")
    else:
        log("[i] История .git не включена (--with-git, если нужна; она есть на GitHub)")
    if (root / "backend/app/utils/ffmpeg").is_dir():
        log("[i] Бинарники ffmpeg (ffmpeg.exe + *.dll) не включены — качаются заново при транскодинге")
    for note in notes:
        log(f"[!] {note}")

    if args.list:
        by_dir: dict[str, list[int]] = {}
        for entry in entries:
            top = entry.rel.split("/", 1)[0]
            bucket = by_dir.setdefault(top, [0, 0])
            bucket[0] += 1
            bucket[1] += entry.size
        log("")
        log(f"{'путь':<28}{'файлов':>8}{'размер':>12}")
        for top in sorted(by_dir, key=lambda key: -by_dir[key][1]):
            count, size = by_dir[top]
            log(f"{top:<28}{count:>8}{human_bytes(size):>12}")
        big = sorted(entries, key=lambda entry: -entry.size)[:5]
        log("")
        log("Самые крупные файлы:")
        for entry in big:
            log(f"  {human_bytes(entry.size):>10}  {entry.rel}")
        log("")
        log("Ничего не создано (--list).")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{safe_label(args.label)}" if args.label else ""
    archive = out_dir / f"{project_name}-{stamp}{suffix}.zip"

    write_zip(archive, root, entries)
    count, missing = verify_zip(archive)
    if missing:
        archive.unlink(missing_ok=True)
        raise SystemExit(f"[!] В архиве нет обязательных файлов: {', '.join(missing)} — бэкап удалён")

    secrets_archive: Path | None = None
    if args.with_secrets:
        secret_entries: list[Entry] = []
        for rel in SECRET_PATHS:
            target = root / rel
            if target.is_file():
                secret_entries.append(Entry(rel, target.stat().st_size))
            elif target.is_dir():
                secret_entries.extend(_walk_tree(
                    root, rel, with_git=False, excluded_dirs=EXCLUDE_DIR_NAMES,
                    file_patterns=("*.log", "*.tmp", "*.bak"),
                ))
        if not secret_entries:
            log("[!] --with-secrets: секретных файлов не найдено")
        else:
            secrets_archive = out_dir / f"{project_name}-{stamp}{suffix}.secrets.zip"
            write_zip(secrets_archive, root, secret_entries)
            log(f"[!] Секреты сохранены ОТДЕЛЬНО: {secrets_archive.name} "
                f"({len(secret_entries)} файлов) — архив не шифрован, не выкладывайте его в git/облако")

    manifest = out_dir / f"{project_name}-{stamp}{suffix}.manifest.txt"
    write_manifest(
        manifest, root=root, archive=archive, entries=entries, notes=notes,
        with_git=args.with_git, with_runtime=args.with_runtime, secrets_archive=secrets_archive,
    )

    oversized = [entry for entry in entries if entry.size > LARGE_FILE_WARN_BYTES]
    for entry in oversized:
        log(f"[!] крупный файл в архиве: {entry.rel} ({human_bytes(entry.size)})")

    removed = prune_old(out_dir, project_name, args.keep)
    for path in removed:
        log(f"[i] удалён старый архив: {path.name}")

    log("")
    log(f"[OK] Архив:    {archive} ({human_bytes(archive.stat().st_size)}, {count} записей)")
    log(f"[OK] Манифест: {manifest}")
    if not args.quiet:
        log(f"[i] SHA-256:   {sha256_of(archive)}")
        if secrets_archive is None:
            log("[i] music.env в бэкап не входит: конфигурацию храните отдельно (--with-secrets)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
