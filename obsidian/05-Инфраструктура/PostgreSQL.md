# PostgreSQL

#tmusic #infra #database

---

## Подключение

| Параметр | Значение |
|----------|----------|
| Database name | **tgmusic** |
| Driver | psycopg2-binary |
| URL template | `postgresql://USER:PASS@HOST:PORT/tgmusic` |
| Пример в env (local) | порт часто **5433** (example) / **5435** (dev machine) |

> Порт и креды — смотреть **только** в `music.env` хоста, не в этой заметке.

---

## Создание БД (пример)

```sql
CREATE USER tgvfs WITH PASSWORD 'CHANGE_ME';
CREATE DATABASE tgmusic OWNER tgvfs;
-- при необходимости:
-- GRANT ALL PRIVILEGES ON DATABASE tgmusic TO tgvfs;
```

---

## Схема

Создание таблиц — через SQLAlchemy `Base.metadata` / миграционные скрипты CloudByte (`scripts/migrate_data.py`).

Основные группы таблиц:
- VFS: `folders`, `folder_closures`, `vfiles`, `tg_accounts`
- Catalog: `music_artists`, `music_albums`
- Auth/admin: `app_users`, `revoked_tokens`, `app_settings`, `admin_audit_events`, `backup_runs`
- Playback: `playback_positions`, `listening_history`, `user_favorites`
- Playlists/shares: `playlists`, `playlist_items`, `share_links`

См. [[Модели БД]]

---

## Startup-инициализации

При старте приложения (`main.lifespan`):
1. `wait_for_database(timeout=15)`
2. `ensure_performance_indexes()`
3. `ensure_auth_schema()`
4. `ensure_trigram_indexes()`
5. `ensure_feature_tables()`
6. `ensure_catalog_schema()`
7. `purge_expired_revocations()`
8. `ensure_admin_bootstrap()`
9. TG self-heal encryption

Если БД недоступна — SPA может стартовать, стриминг деградирует (manager.start в try/except).

---

## Операции

| Задача | Команда / путь |
|--------|----------------|
| Backup | `pg_dump tgmusic` |
| Restore | `pg_restore` / `psql` |
| Миграция из CloudByte | `python scripts/migrate_data.py` |
| Аудит TG-шифрования | `python scripts/secure_tg_accounts.py --check` |

---

## Docker note

PostgreSQL **не** входит в `docker-compose.yml` tmusic. Контейнер приложения должен уметь ходить на БД:
- `host.docker.internal` (extra_hosts уже настроен), или
- внешняя сеть / managed PG

---

Связано: [[Модели БД]] · [[music.env]] · [[Операционные скрипты]]

[[00-Главная]]
