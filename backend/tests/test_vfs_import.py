import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.base import Base
from app.models.vfs import Folder, FolderClosure, TGAccount, VFile
from app.services.vfs_import_service import (
    ImportSession,
    _decode_copy_row,
    _resolve_source_folder,
    _scan_structure,
    _select_subtree,
    _SESSION_LOCK,
    _SESSIONS,
    run_vfs_import,
)

client = TestClient(app)


def _synthetic_dump():
    return "\n".join(
        [
            "PostgreSQL database dump",
            "COPY public.folder_closures (ancestor_id, descendant_id, depth) FROM stdin;",
            "1\t1\t0",
            "1\t2\t1",
            "2\t2\t0",
            "1\t3\t1",
            "2\t3\t2",
            "3\t3\t0",
            "\\.",
            "COPY public.folders (id, name, parent_id, created_at, updated_at, is_trash, is_starred, color, note, cached_total_size, cached_file_count, custom_icon) FROM stdin;",
            "1\tMusic\t\\N\t2026-01-01 00:00:00\t2026-01-01 00:00:00\tf\tf\t\\N\t\\N\t0\t0\t\\N",
            "2\tAlbum\t1\t2026-01-01 00:00:00\t2026-01-01 00:00:00\tf\tf\t\\N\t\\N\t0\t0\t\\N",
            "3\tOld\t2\t2026-01-01 00:00:00\t2026-01-01 00:00:00\tt\tf\t\\N\t\\N\t0\t0\t\\N",
            "\\.",
            "COPY public.vfiles (id, name, folder_id, tg_message_id, msg_ids, tg_file_id, mime_type, size, created_at, updated_at, is_trash, is_starred, note, is_encrypted, encryption_salt, account_map, pending_deletion, encryption_header, custom_icon) FROM stdin;",
            "10\tTrack One.mp3\t2\t44\t\\N\t\\N\taudio/mpeg\t123\t2026-01-01 00:00:00\t2026-01-01 00:00:00\tf\tf\t\\N\tf\t\\N\t{\"7\":{\"msg_ids\":\"44\"}}\tf\t\\N\t\\N",
            "11\tcover.jpg\t2\t\\N\t\\N\t\\N\timage/jpeg\t20\t2026-01-01 00:00:00\t2026-01-01 00:00:00\tf\tf\t\\N\tf\t\\N\t{}\tf\t\\N\t\\N",
            "12\tTrash.mp3\t2\t45\t\\N\t\\N\taudio/mpeg\t123\t2026-01-01 00:00:00\t2026-01-01 00:00:00\tt\tf\t\\N\tf\t\\N\t{}\tf\t\\N\t\\N",
            "\\.",
        ]
    ) + "\n"


def test_copy_parser_decodes_postgres_escapes():
    values = _decode_copy_row("a\tb\\nc\t\\N\t\\x41\t\\101", ["one", "two", "three", "four", "five"])
    assert values == {"one": "a", "two": "b\nc", "three": None, "four": "A", "five": "A"}


def test_structure_scan_resolves_music_subtree(tmp_path):
    path = tmp_path / "backup.sql"
    path.write_text(_synthetic_dump(), encoding="utf-8")
    snapshot = _scan_structure(path)
    root_id, root_path = _resolve_source_folder(snapshot, "Music", None)
    assert root_id == 1
    assert root_path == "Music"
    assert _select_subtree(snapshot, root_id) == {1, 2}
    assert snapshot.table_counts["vfiles"] == 3


def test_structure_scan_rejects_copy_from_program(tmp_path):
    path = tmp_path / "unsafe.sql"
    path.write_text(
        "PostgreSQL database dump\nCOPY public.folders (id) FROM PROGRAM 'id';\n",
        encoding="utf-8",
    )
    try:
        _scan_structure(path)
    except ValueError as exc:
        assert "COPY FROM PROGRAM" in str(exc)
    else:
        raise AssertionError("unsafe COPY statement was accepted")


def test_run_import_merges_vfs_in_isolated_database(tmp_path):
    path = tmp_path / "backup.sql"
    raw = _synthetic_dump().encode("utf-8")
    path.write_bytes(raw)
    snapshot = _scan_structure(path)
    source_root_id, source_path = _resolve_source_folder(snapshot, "Music", None)
    selected_ids = _select_subtree(snapshot, source_root_id)
    import_id = "a" * 32
    state = ImportSession(
        import_id=import_id,
        path=path,
        display_name="backup.sql",
        size_bytes=len(raw),
        digest=hashlib.sha256(raw).hexdigest(),
        actor_id=1,
        source_folder_id=source_root_id,
        source_folder_path=source_path,
        selected_folder_ids=selected_ids,
        target_root_id=None,
        include_non_audio=False,
        snapshot=snapshot,
        preview={"summary": {"can_run": True}, "warnings": []},
        created_at=time.time(),
    )
    with _SESSION_LOCK:
        _SESSIONS[import_id] = state
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[TGAccount.__table__, Folder.__table__, FolderClosure.__table__, VFile.__table__],
    )
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(TGAccount(id=7, name="Primary"))
        db.commit()
        result = run_vfs_import(db, import_id, actor_id=1, confirm=True)
        assert result["files_imported"] == 1
        assert result["folders_created"] == 2
        assert db.query(VFile).count() == 1
        imported = db.query(VFile).one()
        assert imported.account_map == {"7": {"msg_ids": "44"}}
    with _SESSION_LOCK:
        _SESSIONS.pop(import_id, None)


def test_vfs_import_routes_are_registered():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/admin/vfs/import/preview" in paths
    assert "/api/admin/vfs/import/{import_id}/run" in paths
    assert "/api/admin/vfs/import/upload" in paths
    assert "/api/admin/vfs/import/{import_id}/children" in paths
    assert "/api/admin/vfs/import/{import_id}/tree-preview" in paths
    assert "/api/admin/vfs/import/{import_id}/tree-run" in paths
    assert "/api/admin/vfs/import/{import_id}/status" in paths


def test_vfs_import_preview_requires_admin_auth():
    response = client.post(
        "/api/admin/vfs/import/preview",
        files={"file": ("backup.sql", _synthetic_dump().encode("utf-8"), "text/plain")},
        data={"source_folder_path": "Music"},
    )
    assert response.status_code in (401, 403)


def test_stage_upload_and_tree_navigation_and_merge(tmp_path):
    from io import BytesIO
    from fastapi import UploadFile
    from app.services.vfs_import_service import (
        stage_vfs_upload,
        get_staged_children,
        preview_tree_import,
        run_tree_import,
        get_import_status,
        _cleanup_sessions,
        _remove_file,
    )

    sql_content = _synthetic_dump().encode("utf-8")
    upload = UploadFile(filename="cloudbyte_backup.sql", file=BytesIO(sql_content))

    staged = stage_vfs_upload(upload, actor_id=1)
    assert staged["ok"] is True
    import_id = staged["import_id"]
    assert staged["total_folders"] >= 2
    assert len(staged["root_folders"]) == 1
    root = staged["root_folders"][0]
    assert root["name"] == "Music"
    assert root["id"] == 1

    # Test get_staged_children for Music
    children = get_staged_children(import_id, actor_id=1, parent_id=1)
    assert children["ok"] is True
    assert len(children["folders"]) == 1
    assert children["folders"][0]["name"] == "Album"

    # Test preview diff against empty DB
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[TGAccount.__table__, Folder.__table__, FolderClosure.__table__, VFile.__table__],
    )
    Session = sessionmaker(bind=engine)
    with Session() as db:
        db.add(TGAccount(id=7, name="Primary"))
        db.commit()

        # Preview selecting Music folder (ID 1)
        prev = preview_tree_import(
            db,
            import_id=import_id,
            actor_id=1,
            selected_folder_ids=[1],
            selected_file_ids=[],
            target_root_id=None,
        )
        assert prev["ok"] is True
        assert prev["summary"]["folders_create"] == 2
        assert prev["summary"]["files_imported"] == 2  # Track One.mp3 + cover.jpg

        # Run import
        res = run_tree_import(
            db,
            import_id=import_id,
            actor_id=1,
            selected_folder_ids=[1],
            selected_file_ids=[],
            target_root_id=None,
            confirm=True,
        )
        assert res["ok"] is True
        assert res["folders_created"] == 2
        assert res["files_imported"] == 2
        assert db.query(Folder).count() == 2
        assert db.query(VFile).count() == 2

        # Check closures were built correctly (ancestor 1 of descendant 2)
        album = db.query(Folder).filter_by(name="Album").one()
        music = db.query(Folder).filter_by(name="Music").one()
        closure = db.query(FolderClosure).filter_by(ancestor_id=music.id, descendant_id=album.id).one()
        assert closure.depth == 1

        # Test IDEMPOTENCY: run preview and import AGAIN with the same backup!
        prev2 = preview_tree_import(
            db,
            import_id=import_id,
            actor_id=1,
            selected_folder_ids=[1],
            selected_file_ids=[],
            target_root_id=None,
        )
        assert prev2["summary"]["folders_create"] == 0
        assert prev2["summary"]["folders_reuse"] == 2
        assert prev2["summary"]["files_imported"] == 0
        assert prev2["summary"]["files_skipped"] == 2

        res2 = run_tree_import(
            db,
            import_id=import_id,
            actor_id=1,
            selected_folder_ids=[1],
            selected_file_ids=[],
            target_root_id=None,
            confirm=True,
        )
        assert res2["folders_created"] == 0
        assert res2["folders_reused"] == 2
        assert res2["files_imported"] == 0
        assert res2["files_skipped"] == 2
        # No extra duplicates in DB
        assert db.query(Folder).count() == 2
        assert db.query(VFile).count() == 2

        status = get_import_status(import_id, actor_id=1)
        assert status["status"] == "completed"


def test_stage_upload_zip_archive():
    import zipfile
    from io import BytesIO
    from fastapi import UploadFile
    from app.services.vfs_import_service import stage_vfs_upload

    bio = BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("CloudByte Auto Backup.sql", _synthetic_dump())
    bio.seek(0)

    upload = UploadFile(filename="CloudByte Database Backup (25.09.2026).zip", file=bio)
    staged = stage_vfs_upload(upload, actor_id=1)
    assert staged["ok"] is True
    assert staged["filename"].endswith(".zip")
    assert len(staged["root_folders"]) == 1
    assert staged["root_folders"][0]["name"] == "Music"


def test_stage_upload_sqlite_insert_dump():
    from io import BytesIO
    from fastapi import UploadFile
    from app.services.vfs_import_service import stage_vfs_upload, get_staged_children

    sqlite_sql = (
        "PRAGMA foreign_keys=OFF;\n"
        "BEGIN TRANSACTION;\n"
        "CREATE TABLE folders (id INTEGER PRIMARY KEY, name TEXT, parent_id INTEGER, created_at TEXT, updated_at TEXT, is_trash INTEGER, is_starred INTEGER, color TEXT, note TEXT, cached_total_size INTEGER, cached_file_count INTEGER, custom_icon TEXT);\n"
        "INSERT INTO \"folders\" VALUES(10,'Library',NULL,'2026-01-01 00:00:00','2026-01-01 00:00:00',0,0,NULL,NULL,0,0,NULL);\n"
        "INSERT INTO \"folders\" VALUES(20,'Electronic',10,'2026-01-01 00:00:00','2026-01-01 00:00:00',0,0,NULL,NULL,0,0,NULL);\n"
        "CREATE TABLE vfiles (id INTEGER PRIMARY KEY, name TEXT, size INTEGER, mime_type TEXT, tg_message_id INTEGER, msg_ids TEXT, tg_file_id TEXT, folder_id INTEGER, is_starred INTEGER, is_trash INTEGER, pending_deletion INTEGER, encryption_salt TEXT, encryption_header TEXT, is_encrypted INTEGER, note TEXT, custom_icon TEXT, account_map TEXT, created_at TEXT, updated_at TEXT);\n"
        "INSERT INTO \"vfiles\" VALUES(100,'Synth.mp3',1024,'audio/mpeg',55,NULL,NULL,20,0,0,0,NULL,NULL,0,NULL,NULL,'{}','2026-01-01 00:00:00','2026-01-01 00:00:00');\n"
        "COMMIT;\n"
    )

    upload = UploadFile(filename="sqlite_dump.sql", file=BytesIO(sqlite_sql.encode("utf-8")))
    staged = stage_vfs_upload(upload, actor_id=1)
    assert staged["ok"] is True
    assert staged["total_folders"] == 2
    assert len(staged["root_folders"]) == 1
    assert staged["root_folders"][0]["name"] == "Library"

    children = get_staged_children(staged["import_id"], actor_id=1, parent_id=10)
    assert len(children["folders"]) == 1
    assert children["folders"][0]["name"] == "Electronic"

    sub_children = get_staged_children(staged["import_id"], actor_id=1, parent_id=20)
    assert len(sub_children["files"]) == 1
    assert sub_children["files"][0]["name"] == "Synth.mp3"


def test_stage_upload_real_cloudbyte_backup_if_present():
    import os
    from pathlib import Path
    from fastapi import UploadFile
    from app.services.vfs_import_service import stage_vfs_upload, get_staged_children

    real_backup = Path("CloudByte Auto Backup 250926_11-05.sql")
    if not real_backup.is_file():
        return

    with open(real_backup, "rb") as f:
        upload = UploadFile(filename=real_backup.name, file=f)
        staged = stage_vfs_upload(upload, actor_id=1)

    assert staged["ok"] is True
    assert staged["total_folders"] > 10000
    assert staged["total_files"] > 200000
    root_names = [f["name"] for f in staged["root_folders"]]
    assert any("Drive" in name for name in root_names)

    # Test lazy loading for one of the root folders
    drive_folder = next(f for f in staged["root_folders"] if "Drive" in f["name"])
    children = get_staged_children(staged["import_id"], actor_id=1, parent_id=drive_folder["id"])
    assert "folders" in children
    assert "files" in children

