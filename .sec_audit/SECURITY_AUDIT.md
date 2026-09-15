# T-Music — Отчёт по безопасности (Security Audit)

**Дата аудита:** 15.09.2026
**Объект:** `K:\Test\tmusic` — автономный стриминговый сервер (FastAPI + React) поверх Telegram-облака
**Метод:** ручное ревью всех исходных файлов (backend 36 py, frontend 14 js/jsx, скрипты, Docker, конфиги) + динамическая верификация критической находки (PoC на реальном uvicorn)
**Классификация:** OWASP Top 10 (2017)

---

## Резюме

| № | Категория OWASP | Вердикт | Ключевые находки |
|---|-----------------|---------|------------------|
| A01 | Инъекции | 🟢 Низкий риск | ORM везде; только LIKE-wildcard минор |
| A02 | Дефекты аутентификации | 🔴 КРИТИЧНО | Дефолтные креды + несолёный SHA-256 + 4-значный PIN + cookie без Secure |
| A03 | Раскрытие конфиденциальных данных | 🔴 КРИТИЧНО | music.env с реальными секретами; читается без аутентификации через A05 |
| A04 | XXE | 🟢 Не применимо | XML не парсится; SVG запрещён |
| A05 | Дефекты контроля доступа | 🔴 КРИТИЧНО | **Path traversal в serve_spa (подтверждён PoC на реальном сервере)**; публичный cover-proxy; REQUIRE_AUTH=false |
| A06 | Ошибки конфигурации | 🟠 Средний | unsafe-inline в CSP; root в Docker; 0.0.0.0 |
| A07 | XSS | 🟢 Низкий риск | React-экранирование; нет innerHTML; unsafe-inline в CSP снижает запас прочности |
| A08 | Небезопасная десериализация | 🟢 Не найдено | Pydantic-валидация; JWT с whitelist алгоритмов |
| A09 | Компоненты с уязвимостями | 🟠 Средний | python-jose (CVE, не поддерживается), pyrogram/passlib заброшены, зависимости не пинируются |
| A10 | Логирование и мониторинг | 🟠 Средний | Аутентификация не логируется; rate limiter реализован, но не подключён |

**Общий вердикт:** приложение спроектировано с хорошим уровнем защиты отдельных механизмов (SSRF-фильтр, bcrypt, JWT-ревокация, magic-bytes валидация), но содержит **одну подтверждённую критическую уязвимость (path traversal)**, которая в связке с хранением всех секретов в `music.env` приводит к **полной компрометации сервиса одним неаутентифицированным GET-запросом**.

---

## 🔴 КРИТИЧЕСКИЕ НАХОДКИ

### C-1. Path Traversal → чтение произвольных файлов без аутентификации

**Файл:** `backend/app/main.py`, строки 105–110
**OWASP:** A05 (также ведёт к A03)

```python
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = FRONTEND_DIST / full_path      # ← путь из URL без проверки
    if file_path.is_file():
        return FileResponse(file_path)          # ← отдаётся любой существующий файл
    return FileResponse(FRONTEND_DIST / "index.html")
```

Путь из URL склеивается с `frontend/dist` **без** `resolve()` и проверки `is_relative_to()` (сравните с корректной реализацией в `cover_cache_service.py:165–202`, где `os.path.basename` + `resolve` + `is_relative_to` применяются).

**PoC (подтверждён на реальном uvicorn + curl, не только в TestClient):**

```
$ curl -s --path-as-is "http://target:8005/..%2F..%2Fmusic.env"
SECRET_KEY=***REDACTED***
SESSION_SECRET=***REDACTED***
ENCRYPTION_RECOVERY_KEY=***REDACTED***
DATABASE_URL=postgresql://tgvfs:***REDACTED***@localhost:5433/tgmusic
...

$ curl -s --path-as-is -o /dev/null -w "%{http_code}" \
    "http://target:8005/..%2F..%2F..%2F..%2F..%2F..%2FWindows/win.ini"
200
```

**Цепочка компрометации (kill chain):**
1. Чтение `music.env` → `SECRET_KEY` → подделка JWT-токена admin → полный доступ к библиотеке и стриму;
2. `SESSION_SECRET` → расшифровка Fernet-сессий Telegram из БД (`tg_accounts.session_string`) → **захват Telegram-аккаунтов кластера**;
3. `ENCRYPTION_RECOVERY_KEY` → расшифровка всего зашифрованного аудио-хранилища;
4. `DATABASE_URL` → прямой доступ к PostgreSQL;
5. Выход за пределы проекта (`../` × N) → чтение произвольных файлов на хосте с правами процесса (в Docker — от root, см. M-4).

**Исправление (немедленно):**

```python
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    # Нормализация + изоляция в пределах dist
    candidate = (FRONTEND_DIST / full_path).resolve()
    if not candidate.is_relative_to(FRONTEND_DIST.resolve()):
        return FileResponse(FRONTEND_DIST / "index.html")
    if candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(FRONTEND_DIST / "index.html")
```

Ещё лучше — заменить ручной catch-all на `app.mount("/", StaticFiles(directory=..., html=True))`, у которого traverse-защита встроена.

**Файлы PoC:** `.sec_audit/poc_path_traversal.py` (TestClient), `.sec_audit/poc_server.py` + `curl --path-as-is` (реальный HTTP).

---

### C-2. Дефолтные учётные данные + несолёный SHA-256

**Файл:** `scripts/migrate_data.py`, строки 330–343
**OWASP:** A02

```python
default_pw_hash = hashlib.sha256(b"admin123").hexdigest()   # несолёный SHA-256!
default_pin_hash = hashlib.sha256(b"1234").hexdigest()      # 4 цифры!
```

- Креды `admin/admin123` и PIN `1234` **публично задокументированы** в `README.md:66`, `start_tmusic.bat:32`, `migrate_data.py:343`.
- Хэши **несолёные** — ломаются по радужным таблицам за секунды.
- Legacy-путь в `app/core/security.py:29–36, 63–68` **бесконечно** принимает несолёный SHA-256. Автоапгрейд на bcrypt есть только при PIN-логине (`auth_service.py:96–98`); при парольном входе пароль `admin123` навсегда останется в слабом хэше.
- В приложении **нет эндпоинта смены пароля** (только `/api/auth/change-pin`) → дефолтный пароль невозможно сменить через UI.
- PIN = 4 цифры (10 000 вариантов). Блокировка 5 попыток/15 минут **только по IP** (`rate_limiter`); в PIN-потоке счётчик `user.failed_login_attempts` не инкрементируется → распределённый перебор из нескольких IP реалистичен (~2–3 дня на 10 адресов).

**Исправление:**
1. Генерировать случайный пароль при первичной инициализации и печатать его один раз (или требовать смену при первом входе).
2. Хэшировать дефолтные креды bcrypt (`hash_password`), убрать несолёный SHA-256 из функции создания.
3. Добавить `/api/auth/change-password` и автоапгрейд legacy-пароля на bcrypt при успешном входе.
4. В `verify_pin`/`verify_password` удалить принятие «raw SHA-256 без соли» (`security.py:64–65`) — оставить только вариант с солью SECRET_KEY.
5. Минимальная длина PIN ≥ 6, либо капать account-lockout и в PIN-поток.

---

### C-3. Секреты в `music.env` считаются скомпрометированными

**Файл:** `music.env` (в корне проекта)
**OWASP:** A03

Файл содержит реальные значения: `SECRET_KEY`, `SESSION_SECRET`, `ENCRYPTION_RECOVERY_KEY`, `ENCRYPTION_FALLBACK_KEYS`, `DATABASE_URL` (логин/пароль), `TG_API_ID`. `TG_API_HASH` в файле замаскирован — единственное исключение.

Положительные моменты: файл в `.gitignore` (в git не попадёт), монтируется в контейнер как volume. Но из-за C-1 он **неаутентифицированно читается по HTTP**.

**Исправление:** после деплоя патча C-1 — **ротация всех ключей** (JWT SECRET_KEY, SESSION_SECRET с перешифровкой TG-сессий, мастер-ключей шифрования аудио, пароля БД). Считать их раскрытыми, если сервис был доступен по сети.

---

## 🟠 НАХОДКИ СРЕДНЕЙ СЕРЬЁЗНОСТИ

### M-1. Публичный прокси обложек без аутентификации
**Файл:** `backend/app/api/library_router.py:68–114` — `GET /api/library/cover` не требует `get_current_user`.

Любой аноним может:
- вызвать загрузку произвольного внешнего URL (`?url=...`) — SSRF-фильтр в `cover_cache_service.is_safe_host` срабатывает (проверка схем, приватных диапазонов, ручная обработка редиректов с ревалидацией — сделано добротно), но остаётся **DNS-rebinding TOCTOU**: `getaddrinfo` резолвит хост при проверке, а `aiohttp` резолвит повторно при соединении — атакующий с «пляшущим» DNS обходит фильтр;
- **заполнить диск**: каждый уникальный URL навсегда кэшируется в `.bin` до 10 МБ (`_do_fetch` → `bin_dest.write_bytes`) — анонимный disk-fill DoS;
- читать содержимое кэша обложек без аутентификации.

**Исправление:** требовать аутентификацию (front-end может передавать cookie в `<img src>` — same-origin), либо как минимум подключить rate limiting и лимит размера кэша. Для защиты от rebinding — резолвить и соединяться по IP с явным `Server:`/SNI, или использовать aiohttp `resolver` с кэшированным адресом.

### M-2. `REQUIRE_AUTH=false` полностью отключает аутентификацию
**Файл:** `backend/app/api/dependencies.py:43–46` — при выключенном флаге любой запрос выполняется от имени `admin`. Одна строка в env — и сервис открыт. Флаг стоит держать только за явным осознанным выбором; стоит добавить предупреждение в лог при старте и запрет в production.

### M-3. CSP с `unsafe-inline` и устаревшие заголовки
**Файл:** `backend/app/main.py:74–81`
- `script-src 'self' 'unsafe-inline'` — главный механизм XSS-защиты фактически ослаблен; для React-приложения inline-скрипты не нужны, уберите `'unsafe-inline'` из `script-src` (в `style-src` допустимо).
- `X-XSS-Protection` (строка 71) устарел и игнорируется современными браузерами — можно удалить.
- Нет `Strict-Transport-Security` (HSTS) — при internet-facing деплое за HTTPS-прокси обязателен.
- `COOKIE_SECURE=false` по умолчанию (`config.py:44`) — сессионная cookie уйдёт по HTTP. Для публичного деплоя: HTTPS + `COOKIE_SECURE=true` + HSTS.

### M-4. Docker: контейнер от root, без ограничений
**Файл:** `Dockerfile`, `docker-compose.yml`
- Нет `USER` — сервис (включая pyrogram-клиенты Telegram) работает от root; в связке с C-1 traversal читает любые файлы контейнера.
- Нет `HEALTHCHECK`, `security_opt: [no-new-privileges]`, `read_only`, лимитов ресурсов.
- Базовые образы не пинируются по digest.

**Исправление:** добавить непривилегированного пользователя (`useradd -r tmusic; USER tmusic`), `no-new-privileges:true`, chown на `/app/cache`, `/app/data`.

### M-5. Аутентификация не логируется
**OWASP:** A10
Логируются SSRF-блокировки, FloodWait и ошибки стрима, но **не логируются**: успешные/неуспешные логины (username, IP), lockout-события, logout, смена PIN, загрузки обложек, стрим-запросы. Для internet-facing сервиса аудит-лог аутентификации — обязательный минимум. Также `SlidingWindowRateLimiter.is_rate_limited()` (rate_limiter.py:50) **реализован, но нигде не вызывается** — общего rate limiting на API нет (только login-lockout): библиотеку можно выкачать целиком, стрим — выжать DoS-ом. Логи только в stdout, без ротации/алертов.

**Исправление:** в `auth_service.py` добавить `logger.info/warning("LOGIN %s ip=%s ok=%s", username, ip, ...)`; подключить `is_rate_limited` middleware (например 60 req/min/IP на `/api/*`).

### M-6. Зависимости: CVE и брошенные библиотеки
**OWASP:** A09
- `requirements.txt` — все версии `>=` (не пинируются): невоспроизводимая сборка, supply-chain риск. Pin точные версии (или диапазоны с lock-файлом).
- **python-jose 3.3.0** — CVE-2024-33663 (algorithm confusion, ECDSA) и CVE-2024-33664 (JWE DoS); проект не поддерживается. В этом коде не эксплуатируется (HS256, `algorithms=[...]` whitelist), но рекомендуется миграция на **PyJWT**.
- **pyrogram 2.0.106** — не развивается с 2023; рассмотреть форк kurigram.
- **passlib 1.7.4** — не поддерживается с 2020, конфликтует с bcrypt ≥ 4.x (шумная ошибка чтения версии). Вариант: перейти на `bcrypt` напрямую.
- Frontend (по package-lock): react 19.3.0, vite 6.4.3 (только dev), zustand 5.0.15 — известных критических CVE для прод-бандла нет.

---

## 🟡 МИНОРНЫЕ НАХОДКИ

1. **LIKE-wildcard** (`artist_repository.py:65`, `album_repository.py:69`): `%`/`_` в поисковом запросе не экранируются — не SQLi (bound-параметр), но пользователь может отправить паттерн `%%%`. Impact: только шум в поиске.
2. **Username enumeration по времени** (`auth_service.py:37–40`): при несуществующем пользователе bcrypt не вызывается → ответ быстрее. Сравните время → валидные логины. Лечится фиктивным bcrypt-вызовом.
3. **`?token=` в query** (`dependencies.py:83–98`): стрим-токен через URL попадает в логи прокси/историю. Frontend этим не пользуется (cookie) — можно ограничить или выпустить отдельные short-lived tickets.
4. **Дефолтный `DATABASE_URL` с захардкоженным паролем** в `config.py:34` и `migrate_data.py:52`.
5. **TRUST_PROXY_HEADERS=false по умолчанию**: за reverse-proxy без этого флага limiter видит IP прокси → 5 неудачных попыток любых пользователей заблокируют всех сразу (self-DoS).
6. **`start_tmusic.bat:35`**: uvicorn на `0.0.0.0` — сервис по умолчанию доступен из LAN; README транслирует это как фичу.
7. **JWT 12 часов** (`ACCESS_TOKEN_EXPIRE_MINUTES=720`) — длинновато для internet-facing; компенсируется ревокацией при logout, но не при смене PIN (старые токены живут до exp).
8. **RAM-кэш без лимита байт** (`ram_cache_service.py`): 15 треков × ~50–100 МБ = до 1.5 ГБ на пользователя при конкурентных стримах.

---

## ✅ ЧТО СДЕЛАНО ХОРОШО (для протокола)

- **SQLi:** весь доступ к БД через SQLAlchemy ORM / параметризованные `text()` с bound-параметрами — инъекций не найдено. Командных инъекций, `eval/exec/pickle/yaml.load` нет нигде.
- **SSRF-фильтр** (`cover_cache_service.py:58–129`): схемы http/https, блокировка RFC1918/loopback/link-local/CGNAT/169.254.169.254, асинхронный DNS, ручная обработка редиректов с ревалидацией каждого хопа.
- **Path traversal в кэше обложек** — сделан правильно (`basename` + `resolve` + `is_relative_to`).
- **Upload-валидация:** magic bytes (PNG/JPEG/GIF/WebP/BMP), SVG запрещён (Stored-XSS превентивно отсечён), лимит 10 МБ.
- **Криптография аудио:** AES-256-GCM с per-file ключами через HKDF, Argon2id/PBKDF2 KDF, уникальные nonce на блок, secure zeroing мастер-ключа в памяти.
- **Аутентификация:** bcrypt, `hmac.compare_digest` (timing-safe), IP+account lockout, JWT с jti и серверной ревокацией при logout, HttpOnly+SameSite=Lax cookie, токен не хранится в localStorage.
- **XSS:** React с авто-экранированием; `dangerouslySetInnerHTML`/`innerHTML` отсутствуют; `bio`/`wikipedia_url` рендерятся как текст.
- **XXE:** XML-парсеров нет вообще.
- **Десериализация:** Pydantic-схемы с валидацией; JWT-декодирование с whitelist `algorithms=[HS256]`.

---

## ПЛАН ДЕЙСТВИЙ ПО ПРИОРИТЕТУ

| Приоритет | Действие | Находка |
|-----------|----------|---------|
| 1 (сегодня) | Патч `serve_spa` (resolve + is_relative_to или StaticFiles(html=True)) | C-1 |
| 2 (сегодня) | Ротация ВСЕХ секретов music.env; смена пароля БД | C-3 |
| 3 (48 ч) | Убрать дефолтные креды из миграции, bcrypt для дефолтов, добавить /change-password, автоапгрейд legacy-хэшей | C-2 |
| 4 (неделя) | Аутентификация на GET /cover + rate limiting API + auth-логирование | M-1, M-5 |
| 5 (неделя) | HTTPS + COOKIE_SECURE + HSTS; убрать unsafe-inline из script-src | M-3 |
| 6 (спринт) | Docker: непривилегированный USER, no-new-privileges, digest-пины | M-4 |
| 7 (спринт) | Pin зависимостей; python-jose → PyJWT; pyrogram → kurigram | M-6 |
| 8 (бэклог) | Минорные пункты 1–8 | 🟡 |

---

## Приложение: артефакты аудита

- `.sec_audit/poc_path_traversal.py` — PoC №1 (TestClient): подтверждает чтение music.env и выход за корень.
- `.sec_audit/poc_server.py` — PoC №2 (реальный uvicorn, порт 8931): запуск + `curl -s --path-as-is "http://127.0.0.1:8931/..%2F..%2Fmusic.env"` → HTTP 200, тело с секретом.
- Просмотрено: 100% backend (*.py), 100% frontend/src, скрипты, Dockerfile, docker-compose.yml, env-файлы, SECURITY.md, тесты.
