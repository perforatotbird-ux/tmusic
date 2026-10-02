# music.env

#tmusic #infra #config

| Файл | Роль |
|------|------|
| `music.env` | Runtime-конфиг (секреты). **В git не попадает.** |
| `music.env.example` | Шаблон с CHANGE_ME и комментариями |
| `.secrets/postgres_password` | Локальный Docker secret для optional db; исключён из Git и Docker image |

Загрузка: `pydantic-settings` → `backend/app/core/config.py` → `Settings`.  
`BASE_DIR` = корень репозитория; `ENV_FILE = BASE_DIR / "music.env"`.

> **В эту Obsidian-базу реальные значения секретов не копируются.**

---

## Переменные

### Application
| Ключ | Default | Описание |
|------|---------|----------|
| PROJECT_NAME | T-Music | |
| ENVIRONMENT | production | `development` разрешает REQUIRE_AUTH=false |
| DEBUG | false | `/docs` только при true |
| HOST | 0.0.0.0 | |
| PORT | 8005 | |

### Database
| Ключ | Описание |
|------|----------|
| DATABASE_URL | **Обязателен.** `postgresql://user:pass@host:port/tgmusic`; для Compose профиль db — хост db:5432 |
| POSTGRES_PASSWORD_FILE | Только контейнер PostgreSQL: /run/secrets/postgres_password; не ключ Settings приложения |

### Security
| Ключ | Default | Описание |
|------|---------|----------|
| SECRET_KEY | random | JWT signing; generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| SESSION_SECRET | — | Fernet для TG-ключей в БД; **backup обязателен** |
| ACCESS_TOKEN_EXPIRE_MINUTES | 720 | JWT TTL |
| MAX_FAILED_LOGIN_ATTEMPTS | 5 | |
| LOCKOUT_MINUTES | 15 | |
| REQUIRE_AUTH | true | false → только development |
| COOKIE_SECURE | false | true при HTTPS |
| TRUST_PROXY_HEADERS | false | true только за trusted proxy |
| ALLOWED_ORIGINS | JSON list | CORS; никогда `"*"` с credentials |
| ADMIN_PASSWORD_HASH | optional | Argon2id PHC или bounded bcrypt. **Зафиксирован (пароль `123`), не менять при тестах** — [[Пароль администратора]] |
| PASSWORD_ARGON2_MEMORY_KIB | 65536 | KiB на KDF, границы 19456–131072 |
| PASSWORD_ARGON2_TIME_COST | 3 | Iterations, границы 2–6 |
| PASSWORD_ARGON2_PARALLELISM | 1 | Lanes, границы 1–4 |
| PASSWORD_HASH_CONCURRENCY | 2 | Проверки/хеширования одновременно на процесс, границы 1–4 |

### Rate limit
| Ключ | Default |
|------|---------|
| API_RATE_LIMIT_PER_MIN | 120 |
| AUTH_RATE_LIMIT_PER_MIN | 10 |

### Streaming / cache
| Ключ | Default |
|------|---------|
| RAM_CACHE_MAX_ITEMS | 15 |
| RAM_CACHE_MAX_BYTES | 512MB |
| PRECACHE_CHUNK_BYTES | 2MB |
| TG_MAX_CONCURRENT_TRANSMISSIONS | 3 |

### Cover proxy
| Ключ | Default |
|------|---------|
| COVER_HTTP_TIMEOUT_SEC | 20 |
| COVER_HTTP_CONNECT_TIMEOUT_SEC | 8 |
| COVER_MAX_IMAGE_BYTES | 10MB |

### Telegram
| Ключ | Описание |
|------|----------|
| TG_API_ID | Telegram api_id |
| TG_API_HASH | api_hash (в БД может быть Fernet) |
| TG_SESSION_STRING | Legacy bootstrap |
| TG_SESSION_NAME | tmusic_session |
| TG_WORKERS | 2 |
| SLEEP_THRESHOLD | 60 |

### Encryption vault (audio)
| Ключ | Описание |
|------|----------|
| ENCRYPTION_RECOVERY_KEY | base64, 32 bytes AES-256 |
| ENCRYPTION_FALLBACK_KEYS | comma-separated legacy keys |

### Optional enrichment
| Ключ |
|------|
| LASTFM_API_KEY |
| DISCOGS_CONSUMER_KEY |
| DISCOGS_CONSUMER_SECRET |

---

## PostgreSQL secret для Compose

`python scripts/init_db_secret.py` создаёт случайный секрет в `.secrets/postgres_password`, без вывода значения и без перезаписи существующего файла. Для Compose приложение читает свою строку подключения из приватного `music.env`; пароль в ней должен совпадать с secret-файлом. См. [[Docker]] и [[PostgreSQL]].

Не переносить реальные строки подключения в эту базу. Для существующего volume смена файла не заменяет ротацию роли через `psql`.

## Fail-fast правила (config.py)

1. Пустой `DATABASE_URL` → `RuntimeError`
2. `REQUIRE_AUTH=false` и `ENVIRONMENT != development` → `RuntimeError`
3. При импорте создаются каталоги: `data/`, `data/sessions/`, `cache/`, `cache/music_images`, `cache/music_covers`

---

## Пароль администратора — запрет на изменение

**Пароль админа всегда `123`** (решение владельца 2026-10-02). Эта строка —
единственный источник истины и она **не должна переписываться при тестировании**.

| Правило | Реализация |
|---|---|
| Тесты не трогают `ADMIN_PASSWORD_HASH` | `backend/tests/password_guard.py` + autouse-фикстура `protect_admin_password` в `conftest.py` |
| Тесты не трогают `app_settings.admin_bootstrap_hash` | та же фикстура + `bootstrap.py` не применяет изменённый env-хэш |
| Хэш применяется один раз | `ensure_admin_bootstrap` не перезаписывает существующий активный аккаунт |

Попытка правки валит прогон с `PasswordMutationError`. Подробно:
[[Пароль администратора]].

## Генерация хэша администратора

Менять только по явному решению владельца — пароль зафиксирован на `123`.

```powershell
python scripts/init_admin.py --hash-only "your-password"
# результат → ADMIN_PASSWORD_HASH=...
```

---

Связано: [[Архитектура]] · [[Архитектура безопасности]] · [[Telegram кластер]] · [[Запуск Windows]]

## Argon2id rollout

См. [[ADR-005 Argon2id Migration]]. Обновить зависимости и пересобрать backend/frontend. Не возвращаться к bcrypt-only версии после появления Argon2id в БД. Для multi-instance сначала обеспечить совместимое чтение на всех экземплярах; текущая ветка одновременно включает запись Argon2id. `SECRET_KEY`, `SESSION_SECRET`, CORS и ключи аудио не менять ради перехода password hashing. Готовый bcrypt-хэш нельзя преобразовать без исходного пароля.

CLI с паролем в аргументе может оставлять его в history/process list: для установки предпочитать `python scripts/init_admin.py` с getpass. Реальные хэши и пароли не сохранять в базе Obsidian.

[[00-Главная]]
