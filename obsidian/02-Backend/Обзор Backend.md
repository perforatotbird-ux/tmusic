# Backend — обзор

#tmusic #backend

Пакет: `backend/app/`. Точка входа: `app.main:app` (uvicorn, порт **8005**).

---

## Структура `app/`

| Модуль | Ответственность |
|--------|-----------------|
| `main.py` | FastAPI, lifespan (DB, TG cluster, bootstrap), middleware: CSRF, rate-limit, security headers, SPA |
| `api/dependencies.py` | `get_current_user`, `get_stream_user`, bearer security |
| `api/auth_router.py` | Login / me / change-password / logout |
| `api/library_router.py` | Artists, albums, stats, cover proxy, covers upload |
| `api/stream_router.py` | Stream token, folder tracks, Range audio |
| `api/admin_vfs_router.py` | Админский VFS CRUD, closure tree, SQL import preview/run |
| `core/config.py` | `Settings` из `music.env`; fail-fast без `DATABASE_URL` |
| `core/security.py` | bcrypt verify, JWT create/verify, reject unsalted SHA-256 |
| `core/database.py` | engine, SessionLocal, indexes, auth schema, trigram |
| `core/rate_limiter.py` | Sliding-window in-process limiter |
| `core/token_store.py` | Revocation JWT (`jti`) |
| `core/csrf.py` | Double-submit CSRF helpers |
| `models/*` | SQLAlchemy 2.0 Mapped |
| `repositories/*` | Data access + LIKE-escape |
| `schemas/*` | Pydantic response/request |
| `services/*` | Business logic, включая `vfs_import_service` |
| `services/vfs_import_service.py` | COPY-парсер PostgreSQL dump, preview, безопасный VFS merge |
| `tg/*` | Multi-account MTProto |
| `utils/*` | IP, time, msg_ids, http headers |

---

## Lifespan (startup / shutdown)

1. `wait_for_database(15s)` — при отказе лог error
2. `ensure_performance_indexes()` / `ensure_auth_schema()` / `ensure_trigram_indexes()` / `ensure_feature_tables()` / `ensure_catalog_schema()`
3. `purge_expired_revocations(db)`
4. `ensure_admin_bootstrap(db)` — bcrypt из `ADMIN_PASSWORD_HASH`
5. `await tg_manager.start()` — self-heal шифрования + параллельный start клиентов
6. Shutdown: `tg_manager.stop()`, `cover_cache.close()`

> При недоступной БД `tg_manager.start()` **не роняет** SPA — стриминг деградирует.

---

## Middleware (порядок важен)

1. **CSRF** — 403 без `X-CSRF-Token` на cookie-mutations
2. **Rate limit** — `/api/*` 120/мин (или env), auth 10/мин → 429 + `Retry-After`
3. **Security headers** — CSP, nosniff, DENY, Referrer-Policy, Permissions-Policy, HSTS при HTTPS
4. **Maintenance middleware** — 503 для публичных API вне режима обслуживания; admin API сохраняет доступ

---

## Связанные заметки

- [[Архитектура]]
- [[Модели БД]]
- [[API Auth]]
- [[API Library]]
- [[API Stream]]
- [[Telegram кластер]]
- [[Тестирование]]
- [[Импорт VFS из SQL]]
- [[Производительность админки]]
- [[Сканирование каталога]]

---

[[00-Главная]]
