"""Staging parser must enforce the same row/line limits as preview (SEC-013)."""
import pytest

from app.services import vfs_import_service as importer


def _stage(tmp_path, dump: str):
    sql_path = tmp_path / "dump.sql"
    sql_path.write_text(dump, encoding="utf-8")
    conn = importer._init_staging_sqlite(tmp_path / "stage.db")
    try:
        return importer._populate_staging_db(sql_path, conn)
    finally:
        conn.close()


def test_staging_rejects_too_many_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "MAX_ROWS", 2)
    dump = (
        "COPY public.folders (id, name) FROM stdin;\n"
        "1\tA\n"
        "2\tB\n"
        "3\tC\n"
        "\\.\n"
    )
    with pytest.raises(importer.VfsImportError, match="слишком много строк"):
        _stage(tmp_path, dump)


def test_staging_rejects_oversized_line(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "MAX_LINE_BYTES", 8)
    dump = "COPY public.folders (id, name) FROM stdin;\n1\tlong-name-value\n\\.\n"
    with pytest.raises(importer.VfsImportError, match="превышает допустимый размер"):
        _stage(tmp_path, dump)
