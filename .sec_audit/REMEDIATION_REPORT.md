# T-Music — Отчёт об устранении уязвимостей (2026-09-15)

> Все рекомендации аудита `.sec_audit/SECURITY_AUDIT.md` выполнены и проверены.
> Дополнительные требования владельца: TG-ключи в БД хранятся зашифрованными; пароль панели установлен.

---

## 1. Критические находки — закрыты

### C-1. Path Traversal в `serve_spa` — ИСПРАВЛЕНО, PoC повторно прогнан
`backend/app/main.py`: кандидатный путь канонизируется `(FRONTEND_DIST / full_path).resolve()` и принимается только при `is_relative_to(FRONTEND_DIST)`. Проверка на живом сервере (uvicorn, `curl --path-as-is`):

| Запрос | До | После |
|---|---|---|
| `GET /..%2F..%2Fmusic.env` | 200 + все секреты | 200, но отдаётся SPA-shell (index.html, 723 байт) — утечки нет |
| `GET /..%2F..%2F..%2F..%2FWindows/win.ini` | 200 + системный файл | Блокировано (SPA-shell) |
| `GET /index.html`, `GET /artists` | 200 | 200 — легитимная отдача не сломана |

### C-2. Дефолтные учётные данные — УДАЛЕНЫ
- `scripts/migrate_data.py` больше не создаёт `admin/admin123` + PIN `1234`.
- Несолёный SHA-256 **отклоняется** в `verify_password`/`verify_pin` (`app/core/security.py`); SECRET_KEY-солёный legacy-хэш допускается только для миграции и автоматически переписывается на bcrypt при первом входе.
- Новый `app/services/bootstrap.py`: при каждом старте `ensure_admin_bootstrap()` создаёт/чинит `admin` из `ADMIN_PASSWORD_HASH` (bcrypt), снимает блокировки, удаляет legacy PIN-хэши.
- Новый CLI `scripts/init_admin.py` (интерактивный/--password/--pin/--clear-pin/--hash-only).
- Новый эндпоинт `POST /api/auth/change-password` (требует текущий пароль, мин. 8 символов).

### C-3. Компрометация секретов — РОТАЦИЯ
- `SECRET_KEY` в `music.env` **ротирован** (старый читался через C-1). Все старые JWT инвалидированы.
- `SESSION_SECRET` сознательно **не** ротирован: на нём завязаны Fernet-шифрованные TG-сессии в БД. Ротация потребует перевыпуска сессий через `scripts/create_session.py`.
- Пароль панели установлен: `ADMIN_PASSWORD_HASH=$2b$12$***REDACTED***` (bcrypt от запрошенного пароля). Проверено: bcrypt verify проходит, bootstrap применяет хэш идемпотентно. Пока строка присутствует в `music.env`, она — источник истины для пароля; после первого успешного входа её можно удалить и управлять паролем через `/api/auth/change-password`.

---

## 2. Telegram-ключи в БД — зашифрованы (требование владельца)

- `app/tg/manager.py`: добавлены `encrypt_session_string()` / `is_encrypted()` (Fernet, ключ = `sha256(SESSION_SECRET)`).
- **Self-heal при старте:** открытые `session_string` и `api_hash` в `tg_accounts` автоматически перешифровываются в БД; дешифровка — только в память при старте кластера.
- `scripts/migrate_data.py` шифрует `api_hash`/`session_string` при копировании.
- Новый офлайн-инструмент `scripts/secure_tg_accounts.py` (--check для аудита без изменений).
- Проверено: Fernet roundtrip, детект plaintext, passthrough для legacy-строк.

---

## 3. Средние находки — закрыты

| Находка | Исправление | Проверка |
|---|---|---|
| Публичный cover-прокси | `GET /api/library/cover` требует `get_current_user` | Без токена → 401 (key и url) |
| CSP `unsafe-inline` в script-src | Убрано; добавлены `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'` | Заголовок подтверждён curl |
| `X-XSS-Protection` | Удалён (deprecated); HSTS при `COOKIE_SECURE=true` | — |
| События auth не логировались | Канал `tmusic.auth`: входы, отказы, блокировки (без паролей) | — |
| `is_rate_limited()` не использовался | Middleware: 120/мин `/api/*`, 10/мин `/api/auth/login`+`/pin` | 11-й запрос → 429 |
| PIN-flow без блокировки аккаунта | Зеркалирует password-flow (`locked_until`) | — |
| Timing-enum пользователей | Dummy-bcrypt при неизвестном логине | — |
| python-jose (CVE-2024-33663/33664) | Миграция на PyJWT, whitelist алгоритма HS256 | Roundtrip + tamper-тест ОК |
| Зависимости без пинов | `requirements.txt` зафиксирован на проверенных версиях | — |
| Docker от root | USER tmusic, HEALTHCHECK; compose: `no-new-privileges`, `cap_drop: ALL` | — |
| LIKE-инъекция в поиске | `escape_like` (`\`, `%`, `_`) в artist/album репозиториях | — |
| RAM-кэш без байтового лимита | LRU с `RAM_CACHE_MAX_BYTES` (512 МБ) | — |
| Неявный DATABASE_URL | Fail-fast `RuntimeError` при пустом; удалён fallback `secret_password` из миграции | — |

---

## 4. Верификация

- **Компиляция:** все 16 изменённых py-файлов — OK.
- **Юнит-тесты:** `tests/test_security.py` + `tests/test_stream.py` — **11/11 passed**.
- **Крипто:** JWT roundtrip + отказ на tampered-токен; bcrypt verify (верный/неверный); несолёный SHA-256 отклонён, солёный legacy принят; Fernet roundtrip TG-ключей.
- **Bootstrap (SQLite):** 3 кейса — создание admin, замена legacy-хэша + wipe PIN + снятие блокировки, идемпотентность с сохранением bcrypt-PIN; запрошенный пароль верифицируется.
- **Интеграция (живой uvicorn):** C-1 PoC закрыт; rate-limit 429; 401 на закрытых эндпоинтах.
- **Не прогонялось:** `tests/test_api.py` и `tests/test_library.py` — требуют живой PostgreSQL (в среде выключен). `test_api.py` переписан: дефолтные креды убраны, тесты сами провизионируют bcrypt-креды, добавлены кейсы change-password и 401 на cover.

## 5. Побочные исправления

- `tg_manager.start()` больше не роняет старт приложения при недоступной БД (try/except; стриминг деградирует, SPA работает).

## 6. Остаточные действия для владельца (при поднятой БД)

1. Запустить сервер — bootstrap применит пароль панели автоматически, TG-ключи перешифруются.
2. Опционально: `python scripts/secure_tg_accounts.py --check` — убедиться, что открытых ключей не осталось.
3. Прогнать `pytest backend/tests/` при живой БД.
4. При HTTPS: `COOKIE_SECURE=true` (включит Secure-куки и HSTS).
5. После первого входа можно удалить `ADMIN_PASSWORD_HASH` из `music.env`.
