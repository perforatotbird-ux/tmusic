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
CREATE USER tgvfs;
-- Выполнить интерактивно в psql: пароль не попадает в SQL history.
\password tgvfs
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

PostgreSQL есть в optional profile `db` (по умолчанию выключен). Перед инициализацией **нового** volume выполнить `python scripts/init_db_secret.py`: пароль создаётся в приватном `.secrets/postgres_password`, без вывода значения. Compose использует `POSTGRES_PASSWORD_FILE`, а не встроенный пароль.

- Приложение в Compose: адрес БД `db:5432`, роль `tmusic`, база `tgmusic`.
- Хостовые инструменты: `127.0.0.1:5432`; публичной привязки порта нет.
- `DATABASE_URL` приложения заполнить в игнорируемом `music.env`, используя пароль из локального secret-файла. URL-кодировать специальные символы, если пароль выбран вручную.
- При внешней БД профиль `db` и локальный secret-файл не требуются; использовать `host.docker.internal` или адрес managed PostgreSQL.

## Ротация существующей БД после раскрытия пароля

> Новый файл или изменение `POSTGRES_PASSWORD_FILE` **не меняет пароль** в уже созданном `tmusic_pgdata`. Не выполнять `docker compose down -v`: это удалит данные.

1. Согласовать окно обслуживания и сделать проверенный приватный бэкап.
2. Подключиться к БД под ролью с правом смены пароля. Для локального Compose:

```powershell
psql -h 127.0.0.1 -p 5432 -U tmusic -d tgmusic -W
```

3. В интерактивном `psql` выполнить `\password tmusic` и ввести новый уникальный пароль в скрытые приглашения; не помещать пароль в SQL, CLI-аргументы, комментарии или чат.
4. Обновить приватные `.secrets/postgres_password` и `music.env` согласованным значением; ограничить доступ к файлам (Unix — 0600, Windows — ACL владельца). Не вставлять значения в Obsidian.
5. Перезапустить приложение, проверить новое подключение и отказ для старого пароля; обновить другие клиенты/бэкапы, использующие эту роль.
6. Отметить ротацию в [[Аудит секретов 2026-10-01]] только после фактической проверки.

Исторический fallback-пароль считать раскрытым во всех местах повторного использования. Перезапись истории Git — отдельная согласованная операция после ротации; она не отзывает секреты и не очищает чужие клоны.

---

Связано: [[Модели БД]] · [[music.env]] · [[Операционные скрипты]]

[[00-Главная]]
