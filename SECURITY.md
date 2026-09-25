# T-Music: Руководство по Безопасности (Security Architecture)

> Приложение T-Music спроектировано с учетом прямого доступа из публичного Интернета (Internet-facing deployment). В архитектуру заложен принцип нулевого доверия (Zero-Trust) к входящим запросам.
>
> **Обновлено 2026-09-24** по текущему hardening-коду и результатам полного аудита OWASP Top 10 (см. `.sec_audit/SECURITY_AUDIT.md`). Дефолтные учётные данные (`admin/admin123`, PIN `1234`) и хэши SHA-256 без соли **удалены и более не поддерживаются**.

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
- **Поддержка стриминга HTML5:** для нативных тегов `<audio>` поддерживается передача токена через query-параметр `?token=`.
- **Хранение секретов:** все секреты находятся в изолированном файле `music.env`, который исключен из git-репозитория. `SECRET_KEY` ротирован 2026-09-15 после компрометации через C-1.

---

## 6. Telegram-ключи: шифрование при хранении

- `session_string` и `api_hash` в таблице `tg_accounts` хранятся **зашифрованными Fernet** (ключ — `sha256(SESSION_SECRET)`).
- Расшифровка происходит только в памяти при старте кластера (`app/tg/manager.py`).
- Незашифрованные (legacy) строки автоматически перешифровываются при старте сервера; офлайн-вариант — `python scripts/secure_tg_accounts.py`.
- Миграция из CloudByte (`scripts/migrate_data.py`) шифрует ключи при копировании.

---

## 7. Инфраструктура

- **Docker:** контейнер работает от непривилегированного пользователя `tmusic`, добавлен `HEALTHCHECK`, включены `no-new-privileges` и сброс capabilities (`cap_drop: ALL`).
- **Зависимости:** `requirements.txt` зафиксирован на проверенных версиях (без `>=`), уязвимые/неподдерживаемые пакеты исключены.
- **Конфигурация:** `DATABASE_URL` обязателен — приложение падает на старте при его отсутствии (никаких неявных учётных данных в коде).
- **RAM-кэш:** ограничен и по числу треков, и по суммарному объёму (`RAM_CACHE_MAX_BYTES`, по умолчанию 512 МБ) — защита от DoS через память.

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

Runbook: `obsidian/02-Backend/Импорт VFS из SQL.md`.
