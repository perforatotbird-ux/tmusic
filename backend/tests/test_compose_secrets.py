"""Local PostgreSQL provisioning security, without Docker or a live DB."""
import importlib.util
import os
from pathlib import Path
import re
import stat
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('init_db_secret', ROOT / 'scripts/init_db_secret.py')
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


def test_compose_has_no_inline_password_or_credential_url():
    text = (ROOT / 'docker-compose.yml').read_text()
    assert not re.search(r'^\s+POSTGRES_PASSWORD\s*:', text, re.M)
    assert not re.search(r'postgres(?:ql)?://[^\s:/]+:[^\s@]+@', text)
    assert 'POSTGRES_PASSWORD_FILE: /run/secrets/postgres_password' in text
    assert 'file: ./.secrets/postgres_password' in text


def test_compose_publishes_database_only_on_loopback():
    text = (ROOT / 'docker-compose.yml').read_text()
    assert '"127.0.0.1:5432:5432"' in text
    assert '"5432:5432"' not in text


@pytest.mark.parametrize('ignore_file', ['.gitignore', '.dockerignore'])
def test_local_secrets_excluded(ignore_file):
    assert '.secrets/' in (ROOT / ignore_file).read_text().splitlines()


def test_secret_creation_is_random_private_and_does_not_print(tmp_path, capsys):
    first = tmp_path / 'one' / 'postgres_password'
    second = tmp_path / 'two' / 'postgres_password'
    provision.create_secret(first)
    provision.create_secret(second)
    a, b = first.read_text().strip(), second.read_text().strip()
    assert a != b
    assert len(a) >= 43
    assert re.fullmatch(r'[A-Za-z0-9_-]+', a)
    assert capsys.readouterr().out == ''
    if os.name == 'posix':
        assert stat.S_IMODE(first.stat().st_mode) == 0o600
        assert stat.S_IMODE(first.parent.stat().st_mode) == 0o700


def test_existing_secret_is_not_overwritten(tmp_path):
    path = tmp_path / 'postgres_password'
    provision.create_secret(path)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        provision.create_secret(path)
    assert path.read_bytes() == before


@pytest.mark.skipif(os.name != 'posix', reason='Unix symlink test')
def test_secret_creation_rejects_existing_symlink(tmp_path):
    target = tmp_path / 'original'
    target.write_text('untouched')
    link = tmp_path / 'postgres_password'
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        provision.create_secret(link)
    assert target.read_text() == 'untouched'


def test_cli_does_not_print_generated_value(tmp_path, capsys):
    with patch.object(provision, 'ROOT', tmp_path):
        assert provision.main() == 0
        value = (tmp_path / '.secrets/postgres_password').read_text().strip()
        assert value not in capsys.readouterr().out
        assert provision.main() == 1
        assert value not in capsys.readouterr().err
