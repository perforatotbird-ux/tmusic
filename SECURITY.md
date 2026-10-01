# T-Music: Руководство по Безопасности (Security Architecture)

> Приложение T-Music спроектировано с учетом прямого доступа из публичного Интернета (Internet-facing deployment). В архитектуру заложен принцип нулевого доверия (Zero-Trust) к входящим запросам.
>
> **Обновлено 2026-09-27** — применены все находки повторного аудита (кроме CORS-regex, оставлен без изменений намеренно). Ключевые изменения этого раунда: CSRF переведён на **fail-closed** с самовосстановлением куки; Subsonic принимает **только выделенный токен** (пароль панели больше не передаётся в query); share-ссылки получили **дефолтный TTL 30 дней**; глобальный лимит тела запросов; rate limit на приём клиентских логов; `ADMIN_PASSWORD_HASH` стал **одноразовым** (смена пароля через панель больше не откатывается рестартом).

---

## 1. Защита от подбора паролей и DoS (Brute-Force & Rate Limiting)

- **Скользящее окно (Sliding-Window Rate Limiter):**
  На уровне ядра (`app.core.rate_limiter`) реализован потокобезопасный ограничитель частоты запросов.
- **Rate-limit API:** не более **120 запросов/мин** на IP для `/api/*` и **10 запросов/мин** для `/api/auth/login` (HTTP 429 + `Retry-After`).
- **Блокировка по IP при переборе паролей:**
  После 5 неудачных попыток IP-адрес клиента блокируется на **15 минут**. Дополнительно действует блокировка на уровне учётной записи (счётчик `failed_login_attempts`, поле `locked_until`).
- **Защита от атак по времени (Timing Attacks):**
  Сверки хэшей производятся через `hmac.compare_digest` / bcrypt. При запросе несуществующего пользователя выполняется «холостая» bcrypt-проверка (`timing_equalizer_hash`), чтобы время ответа не раскрывало факт существования логина.
- **Аудит событий аутентификации:**
  Все входы, блокировки и отказы пишутся в лог-канал `tmusic.auth` (без паролей).

---

## 2. Защита от SSRF (Server-Side Request Forgery)

Приложение позволяет загружать обложки по внешним URL. Для исключения атак на локальную сеть и внутренние сервисы реализован многоуровневый фильтр:
- **Асинхронный неблокирующий DNS-резолвинг:** Вызов `getaddrinfo` в отдельном пуле потоков без заморозки event loop asyncio.
- **Блокировка приватных и зарезервированных диапазонов:**
  - `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` (RFC 1918)
  - `127.0.0.0/8`, `::1` (Loopback)
  - `169.254.0.0/16`, `fe80::/10` (Link-Local)
  - `100.64.0.0/10` (Carrier-Grade NAT)
  - `169.254.169.254` (Метаданные облачных провайдеров AWS, GCP, Azure, DigitalOcean)
- **Только протоколы `http` и `https`:** Схемы `file://`, `ftp://`, `gopher://` строго отсекаются.
- **Прокси обложек требует аутентификации:** `GET /api/library/cover` закрыт `get_current_user` — открытый прокси не доступен анонимному клиенту.

---

## 3. Защита от Path Traversal и Stored XSS

- **SPA-роутинг (`serve_spa`):** catch-all маршрут `/{full_path:path}` получает уже percent-декодированный ввод, поэтому путь проверяется после канонизации:
  ```python
  candidate = (FRONTEND_DIST / full_path).resolve()
  if candidate.is_relative_to(FRONTEND_DIST) and candidate.is_file():
      return FileResponse(candidate)
  ```
  Запросы вида `/..%2F..%2Fmusic.env` больше не отдают файлы вне `frontend/dist` (закрыта критическая уязвимость C-1).
- **Изоляция директорий кэша:**
  Пути к файлам кэша обложек нормализуются через `Path.resolve()` с проверкой `is_relative_to(cache_root)`.
- **Валидация форматов по сигнатурам (Magic Bytes):**
  Формат загружаемых изображений проверяется по первым байтам (PNG, JPEG, WebP, GIF).
  **SVG намеренно запрещён**, так как XML/SVG файлы могут содержать встроенный вредоносный JavaScript (Stored XSS).
- **Лимит на размер загружаемых файлов:** Жёсткое ограничение 10 МБ на файл.

---

## 4. HTTP Заголовки Безопасности (Security Headers)

Сервер автоматически добавляет к каждому HTTP-ответу следующие заголовки:
- `X-Content-Type-Options: nosniff` — блокирует попытки MIME-sniffing в браузере.
- `X-Frame-Options: DENY` — предотвращает встраивание плеера во фреймы сторонних сайтов (Clickjacking defense).
- `Referrer-Policy: strict-origin-when-cross-origin` — защищает приватные URL при переходе на внешние ресурсы.
- `Content-Security-Policy` — `default-src 'self'`; `script-src 'self'` (**без** `'unsafe-inline'`); `object-src 'none'`; `base-uri 'self'`; `frame-ancestors 'none'`. `'unsafe-inline'` оставлен только для `style-src` (inline-стили React).
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` — выставляется при `COOKIE_SECURE=true` (HTTPS-развёртывание).
- Устаревший `X-XSS-Protection` удалён (современные браузеры игнорируют его, а его поведение может вредить).

---

## 5. Аутентификация и Сессии

- **Только bcrypt-хэши:** хэши SHA-256 без соли отвергаются; SECRET_KEY-солёный legacy-вариант допускается лишь для миграции и автоматически переписывается на bcrypt при первом успешном входе.
- **Первичное provisionирование администратора:** при старте `app/services/bootstrap.py` создаёт/чинит учётную запись `admin` по bcrypt-хэшу из `ADMIN_PASSWORD_HASH` (music.env). Ручной путь — `python scripts/init_admin.py`.
- **Единственный способ входа — логин + пароль** (`POST /api/auth/login`): быстрый PIN-вход (`/api/auth/pin`, `/api/auth/change-pin`, колонка `pin_hash`) удалён.
- **Смена пароля:** `POST /api/auth/change-password` (минимум 8 символов, требуется текущий пароль).
- **JWT:** библиотека PyJWT (python-jose исключён — CVE-2024-33663/33664), алгоритм ограничен белым списком `HS256`, у каждого токена `jti`; отзыв серверной стороной через таблицу `revoked_tokens` (`/api/auth/logout`).
- **Поддержка стриминга HTML5:** для нативных тегов `<audio>` поддерживается передача токена через query-параметр `?token=` (короткоживущий scope="stream", 10 минут).
- **Fail-closed CSRF:** cookie-аутентифицированная мутация **без** csrf-куки отклоняется (403, код `csrf_cookie_missing`); клиент автоматически пере-запрашивает куку через `GET /api/auth/csrf` и повторяет запрос. Тихих обходов больше нет. Проверка пропускается только при well-formed `Authorization: Bearer <token>` (любой другой заголовок — включая мусор, который HTTPBearer не парсит, — проверку НЕ отменяет, иначе аутентификация молча откатывалась на куку жертвы).
- **Subsonic:** в query-строке принимается только выделенный per-user токен (`p=<token>` / `t=md5(token+salt)`) — основной пароль панели больше не передаётся в URL. Ротация токена: `POST /api/subsonic/credentials/rotate`.
- **Хранение секретов:** все секреты находятся в изолированном файле `music.env`, который исключен из git-репозитория. `SECRET_KEY` ротирован 2026-09-15 после компрометации через C-1.
- **Одноразовый `ADMIN_PASSWORD_HASH`:** env-хэш применяется при старте только когда значение изменилось (ротация) либо аккаунта нет; смена пароля в панели переживает рестарт. Рекомендуемая гигиена — удалить переменную после первого запуска.

---

## 6. Telegram-ключи: шифрование при хранении

- `session_string` и `api_hash` в таблице `tg_accounts` хранятся **зашифрованными Fernet** (ключ — `sha256(SESSION_SECRET)`).
- Расшифровка происходит только в памяти при старте кластера (`app/tg/manager.py`).
- Незашифрованные (legacy) строки автоматически перешифровываются при старте сервера; офлайн-вариант — `python scripts/secure_tg_accounts.py`.
- Миграция из CloudByte (`scripts/migrate_data.py`) шифрует ключи при копировании.

---

## 7. Инфраструктура

- **Docker:** контейнер работает от непривилегированного пользователя `tmusic`, добавлен `HEALTHCHECK`, включены `no-new-privileges` и сброс capabilities (`cap_drop: ALL`).
- **Зависимости:** `requirements.txt` зафиксирован на проверенных версиях (без `>=`), уязвимые/неподдерживаемые пакеты исключены. 2026-09-29: закрыты 61 finding pip-audit (Starlette 1.7.0, python-multipart 0.0.32, aiohttp 3.14.3, cryptography 50.0.1, PyJWT 2.15.1, pyzipper 0.4.0, pydantic-settings 2.15.0, python-dotenv 1.2.3); TG-стек переименован в поддерживаемые дистрибутивы `pyrofork` / `TgCrypto-pyrofork` (импорт `pyrogram`/`tgcrypto` без изменений).
- **Конфигурация:** `DATABASE_URL` обязателен — приложение падает на старте при его отсутствии (никаких неявных учётных данных в коде).
- **RAM-кэш:** ограничен и по числу треков, и по суммарному объёму (`RAM_CACHE_MAX_BYTES`, по умолчанию 512 МБ) — защита от DoS через память.
- **Лимит тела запросов:** все `/api/*` и `/rest/*` мутации ограничены 1 МБ **фактических** байт (pure-ASGI middleware измеряет тело в потоке и реплеит его дальше — заголовок `Content-Length` может отсутствовать при chunked или врать). Эндпоинты загрузок с собственными лимитами — VFS-импорт 256 МБ, обложки/аватарки ~10 МБ — исключены.
- **TrustedHost:** `TRUSTED_HOSTS` (по умолчанию `["*"]` — без изменений) включает Starlette TrustedHostMiddleware: чужой Host — 400 до роутинга, защита от Host-poisoning share-ссылок (`request.base_url`).
- **CORS:** private-range regex (`allow_origin_regex`) отключается через `CORS_ALLOW_LAN=false`, когда все клиенты покрыты явными `ALLOWED_ORIGINS`.
- **Клиентские логи:** приёмник `POST /api/system/client-logs` (не требует аутентификации) дополнительно ограничен 30 батчами/мин/IP поверх общего лимита API.
- **FFmpeg вынос:** переменная `FFMPEG_BIN_DIR` позволяет указать системный каталог ffmpeg вместо vendored-бинарей внутри `app/utils/`.
- **Share-ссылки:** дефолтный срок жизни — `SHARE_DEFAULT_TTL_HOURS` (30 дней); `expires_in_hours=0` в API — явная бессрочность.

---

## 8. Безопасный импорт VFS из Replit SQL backup

- Endpoint доступен только admin-пользователю с permission `admin.backups.manage`; cookie mutations защищены CSRF.
- Файл принимается multipart upload, проверяется как UTF-8 plain PostgreSQL dump и ограничен 256 МБ.
- Staging использует UUID-имя в `data/vfs_imports/`, TTL 1 час; клиентский filename не используется как filesystem path.
- SQL никогда не исполняется через `psql`, `sqlalchemy.text()` или shell. Разрешённый parser извлекает только `folders`, `folder_closures`, `vfiles` и безопасные metadata `tg_accounts` для сопоставления аккаунтов.
- `COPY FROM PROGRAM`, бинарный формат, NUL/invalid UTF-8 и неподтверждённые структуры отклоняются.
- Preview не возвращает raw SQL, `session_string`, `api_hash`, `account_map`, `msg_ids` или encryption headers.
- Импорт reuses existing folders, пропускает конфликты файлов, выполняет одну DB-транзакцию и перестраивает `folder_closures`.
- Backup содержит только ссылки на Telegram-сообщения; совместимость TG accounts и encryption keys проверяется предупреждениями preview.
- Распакованный SQL-поток ограничен `MAX_IMPORT_DECOMPRESSED_BYTES` (1 ГБ) — защита от zip-бомб; частичные файлы при обрыве удаляются.

Runbook: `obsidian/02-Backend/Импорт VFS из SQL.md`.

---

## 9. Раунд hardening 2026-09-29

- **Транскодер:** расширение временного файла — allowlist `[a-z0-9]{1,10}` (`safe_src_ext`), иначе `audio`. Crafted-имя трека вида `x.mp3/../../evil` больше не выводит запись за пределы `TRANSCODE_CACHE_DIR`.
- **Тесты как спецификация безопасности:** `test_subsonic.py` переписан под token-only `/rest` (панельный пароль в `p=` — явный regression-тест на отказ); route-тесты переведены на `app.openapi()` (вложенность `app.routes` поменялась в FastAPI 0.14x).
- **Проверено:** 124/124 pytest зелёных как на новом стеке (FastAPI 0.141.1 + Starlette 1.7.0), так и на старом; `pip-audit` — 0 findings; `ruff` — без новых замечаний.


## PostgreSQL secrets и история Git (2026-10-01)

- Optional Compose profile `db` использует `POSTGRES_PASSWORD_FILE` и локальный Docker secret `.secrets/postgres_password`, а не встроенный пароль. Файл исключён из Git и Docker build context; Unix-права создаваемого файла — 0600.
- Публикация порта PostgreSQL ограничена `127.0.0.1:5432`; приложение внутри Compose подключается к `db:5432`.
- `scripts/init_db_secret.py` создаёт случайный пароль только для нового развёртывания, без вывода значения и без перезаписи файла. `DATABASE_URL` остаётся в приватном `music.env`.
- Старый fallback-пароль обнаружен в доступной истории Git. Его действительность не проверялась; удалить из HEAD недостаточно. Ротация на рабочих стендах и force-push для очистки истории не выполнены автоматически.
- Для существующего PostgreSQL volume сменить пароль роли интерактивно (`\password`), обновить приватные конфиги и проверить отказ для старого значения. Не удалять volume. См. runbook в Obsidian `PostgreSQL` и `Аудит секретов 2026-10-01`.
