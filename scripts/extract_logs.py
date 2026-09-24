"""
T-Music Log Extractor
=====================
Lists / extracts / pretty-prints rotated log archives from
backend/storage/log (AES-256 ZIP, password from LOG_ARCHIVE_PASSWORD or
--password).

Usage:
  python scripts/extract_logs.py list
  python scripts/extract_logs.py show                 # tail all active logs (human format)
  python scripts/extract_logs.py extract backend.log.20260923-120000.zip
  python scripts/extract_logs.py extract --all -o extracted/
  python scripts/extract_logs.py jsonl backend.log    # print active JSONL as-is (LLM feed)

Exit codes: 0 ok, 1 error, 2 bad password.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
LOG_DIR = BASE / "backend" / "storage" / "log"

LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def _password(args) -> str | None:
    if getattr(args, "password", None):
        return args.password
    env = BASE / "music.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("LOG_ARCHIVE_PASSWORD="):
                return line.split("=", 1)[1].strip()
    return None


def _archives(stream: str) -> list[Path]:
    return sorted(
        (p for p in LOG_DIR.glob(stream + ".*.zip")),
        key=lambda p: p.name,
    )


def cmd_list(args) -> int:
    if not LOG_DIR.exists():
        print(f"Log dir does not exist: {LOG_DIR}")
        return 1
    print(f"LOG_DIR: {LOG_DIR}\n")
    for stream in ("backend.log", "frontend.log"):
        active = LOG_DIR / stream
        size = active.stat().st_size if active.exists() else 0
        print(f"{stream}: {'ACTIVE' if active.exists() else 'missing'} ({size} bytes)")
        for a in _archives(stream):
            print(f"  archive: {a.name} ({a.stat().st_size} bytes)")
        print()
    return 0


def _read_zip(path: Path, password: str | None) -> bytes:
    if not password:
        print("Password is required for encrypted log archives", file=sys.stderr)
        sys.exit(1)
    import pyzipper
    try:
        with pyzipper.AESZipFile(path, "r") as zf:
            zf.setpassword(password.encode("utf-8"))
            name = zf.namelist()[0]
            return zf.read(name)
    except RuntimeError as e:
        if "password" in str(e).lower() or "Bad password" in str(e):
            print(f"Bad password for {path.name}", file=sys.stderr)
            sys.exit(2)
        raise


def cmd_extract(args) -> int:
    pw = _password(args)
    out_dir = Path(args.out) if args.out else LOG_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    targets: list[Path] = []
    if args.all:
        for stream in ("backend.log", "frontend.log"):
            targets.extend(_archives(stream))
    elif args.files:
        for f in args.files:
            p = Path(f)
            if not p.exists():
                p = LOG_DIR / f
            if not p.exists():
                print(f"Not found: {f}", file=sys.stderr)
                return 1
            targets.append(p)
    else:
        print("Provide archive names or --all", file=sys.stderr)
        return 1

    for p in targets:
        data = _read_zip(p, pw)
        dest = out_dir / (p.name.rsplit(".zip", 1)[0] + ".log")
        dest.write_bytes(data)
        print(f"extracted {p.name} -> {dest} ({len(data)} bytes)")
    return 0


def _fmt_human(obj: dict) -> str:
    ts = obj.get("ts", "?")
    level = obj.get("level", "?")
    name = obj.get("logger", "?")
    msg = obj.get("msg", "")
    color = {"DEBUG": "\033[36m", "INFO": "\033[32m", "WARNING": "\033[33m",
             "ERROR": "\033[31m", "CRITICAL": "\033[41m"}.get(level, "")
    reset = "\033[0m" if color else ""
    line = f"{ts} {color}{level:<8}{reset} {name}: {msg}"
    skip = {"ts", "level", "logger", "msg", "exc"}
    extras = [f"{k}={v}" for k, v in obj.items() if k not in skip and v is not None]
    if extras:
        line += "  (" + ", ".join(extras) + ")"
    if obj.get("exc"):
        line += "\n" + obj["exc"]
    return line


def _iter_jsonl(data: bytes):
    for raw in data.decode("utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            yield {"ts": "?", "level": "?", "logger": "?", "msg": raw}


def cmd_show(args) -> int:
    pw = _password(args)
    min_rank = LEVEL_ORDER.get((args.min_level or "INFO").upper(), 20)
    streams = [args.stream] if args.stream else ["backend.log", "frontend.log"]
    out = []
    for stream in streams:
        active = LOG_DIR / stream
        if active.exists():
            for obj in _iter_jsonl(active.read_bytes()):
                out.append(obj)
        if not args.no_archives:
            for a in _archives(stream):
                for obj in _iter_jsonl(_read_zip(a, pw)):
                    out.append(obj)
    out.sort(key=lambda o: str(o.get("ts", "")))
    if args.tail:
        out = out[-args.tail:]
    for obj in out:
        if LEVEL_ORDER.get(obj.get("level", "INFO"), 20) >= min_rank:
            print(_fmt_human(obj))
    return 0


def cmd_jsonl(args) -> int:
    """Dump everything as raw JSON Lines (ideal for LLM context windows)."""
    pw = _password(args)
    streams = [args.stream] if args.stream else ["backend.log", "frontend.log"]
    rows = []
    for stream in streams:
        active = LOG_DIR / stream
        if active.exists():
            rows.extend(_iter_jsonl(active.read_bytes()))
        if not args.no_archives:
            for a in _archives(stream):
                rows.extend(_iter_jsonl(_read_zip(a, pw)))
    rows.sort(key=lambda o: str(o.get("ts", "")))
    if args.tail:
        rows = rows[-args.tail:]
    for obj in rows:
        print(json.dumps(obj, ensure_ascii=False))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--password", help="ZIP password (default: LOG_ARCHIVE_PASSWORD from music.env)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List active logs and archives")

    sp = sub.add_parser("show", help="Pretty-print logs (human readable)")
    sp.add_argument("--stream", choices=["backend.log", "frontend.log"])
    sp.add_argument("--min-level", default="INFO", help="DEBUG|INFO|WARNING|ERROR")
    sp.add_argument("--tail", type=int, default=200)
    sp.add_argument("--no-archives", action="store_true")

    sp = sub.add_parser("jsonl", help="Raw JSON Lines dump (LLM friendly)")
    sp.add_argument("--stream", choices=["backend.log", "frontend.log"])
    sp.add_argument("--tail", type=int)
    sp.add_argument("--no-archives", action="store_true")

    sp = sub.add_parser("extract", help="Decrypt+extract archives to plain .log")
    sp.add_argument("files", nargs="*", help="Archive file names")
    sp.add_argument("--all", action="store_true", help="Extract all archives")
    sp.add_argument("-o", "--out", default=None, help="Output dir (default: LOG_DIR)")

    args = p.parse_args()
    return {"list": cmd_list, "show": cmd_show, "jsonl": cmd_jsonl, "extract": cmd_extract}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
