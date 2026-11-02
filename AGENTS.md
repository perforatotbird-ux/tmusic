# Project workflow

## Obsidian documentation (required)

The project knowledge base lives in `obsidian/`. Read the relevant existing notes
before changing code or configuration. After each completed stage of work, update
the affected notes immediately, not only at the end of a task.

Document actual behavior, configuration changes, safe operational instructions,
checks performed, and unresolved deployment actions. Link new notes from
`obsidian/00-Главная.md` and related notes. Follow the existing Russian-language
style and wikilinks. See `obsidian/08-Разработка/Чеклист разработки.md`.

Never copy real passwords, tokens, connection strings, private keys or credential
hashes into code, documentation, logs or chat. Use placeholders/variable names.
Do not claim production rotation/deployment or Git history cleanup was completed
when only local files were changed. History rewriting, force-push, and live
credential rotation require explicit coordination; never delete DB volumes to
rotate a password.

## Admin password is fixed (owner decision 2026-10-02)

The panel password is always `123`, and `ADMIN_PASSWORD_HASH` in `music.env` is
the single source of truth for it. That file is gitignored, so this rule cannot
be enforced by version control — it is enforced in code:

- No test or script may write `ADMIN_PASSWORD_HASH` in `music.env`, or the
  `app_settings.admin_bootstrap_hash` row, or the `admin` row of the live
  database. Run backend tests in a disposable schema:
  `TMUSIC_TEST_SCHEMA=tmusic_test pytest` — `conftest.py` creates the schema,
  bootstraps the admin row there and drops the schema afterwards. Running
  without it against the live database is refused unless
  `TMUSIC_ALLOW_LIVE_DB_TESTS=1` is set explicitly. The autouse
  `protect_admin_password` fixture still guards `music.env` and, on the live
  database only, checks that the panel still accepts `123`. Use
  `_AdminPasswordSandbox` for any test that needs a known admin password; it
  restores the original hash in `finally`.
- If the owner is locked out, run `python scripts/restore_admin_password.py`.
  It is idempotent and prints no secrets.
- Regenerating the hash is not part of routine work or test runs. Rotate the
  password only on an explicit owner request, via `scripts/init_admin.py`
  (interactive `getpass`) — never by editing the file during a test session.
- Never write to `music.env` as a way to "test" file handling. Verify with a
  temporary path or a backup copy. The file holds every deployment secret and is
  not recoverable from git if destroyed.

See `obsidian/04-Безопасность/Пароль администратора.md`.
