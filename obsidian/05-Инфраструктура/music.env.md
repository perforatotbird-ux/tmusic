# music.env

#tmusic #infra #config

| Файл | Роль |
|------|------|
| `music.env` | Runtime-конфиг (секреты). **В git не попадает.** |
| `music.env.example` | Шаблон с CHANGE_ME и комментариями |

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
| DATABASE_URL | **Обязателен.** `postgresql://user:pass@host:port/tgmusic` |

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
| ADMIN_PASSWORD_HASH | optional | bcrypt; источник истины пока строка в env |

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

## Fail-fast правила (config.py)

1. Пустой `DATABASE_URL` → `RuntimeError`
2. `REQUIRE_AUTH=false` и `ENVIRONMENT != development` → `RuntimeError`
3. При импорте создаются каталоги: `data/`, `data/sessions/`, `cache/`, `cache/music_images`, `cache/music_covers`

---

## Генерация хэша администратора

```powershell
python scripts/init_admin.py --hash-only "your-password"
# результат → ADMIN_PASSWORD_HASH=...
```

---

Связано: [[Архитектура]] · [[Архитектура безопасности]] · [[Telegram кластер]] · [[Запуск Windows]]

[[00-Главная]]
