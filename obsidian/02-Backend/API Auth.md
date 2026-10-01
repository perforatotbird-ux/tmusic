# API — Auth

#tmusic #backend #api #security

Prefix: `/api/auth` · Router: `backend/app/api/auth_router.py`

Авторизация клиента: **HttpOnly cookie** `access_token` (SameSite=Lax) + **Bearer** для API-клиентов. На мутирующих запросах — header `X-CSRF-Token` (double-submit).

См. [[ADR-004 Cookie Auth CSRF]] · [[API Library]]

---

## Эндпоинты

### POST `/api/auth/login`
| | |
|--|--|
| Body | `{ username, password }` |
| 200 | `TokenResponse` — JWT + `expires_in` |
| 401 | Неверные креды / блокировка |
| 429 | Auth rate-limit (env `AUTH_RATE_LIMIT_PER_MIN`, default 10/мин) |

Побочные эффекты:
- cookie `access_token` (httponly)
- cookie `csrf_token` (readable, for double-submit)
- счётчик lockout: 5 fail → `locked_until` +15 мин (env)
- аудит: лог-канал `tmusic.auth`

**Нет** PIN-входа — удалён после аудита.

### GET `/api/auth/me`
- Auth: cookie/Bearer
- 200: `{ id, username, is_active }`
- 401: нет/невалиден JWT

### POST `/api/auth/change-password`
- Body: `{ current_password, new_password }`
- Требует корректный текущий пароль; новый — **мин. 8 символов**
- Инкремент `token_version` → все старые JWT отклоняются
- 400: текущий пароль неверен

### POST `/api/auth/logout`
- Server-side revoke JWT (`revoked_tokens` по `jti`)
- Очистка cookie
- Доступен и с Bearer, и с cookie

---

## Контракт TokenResponse (схема)

```
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "expires_in": 43200   // ACCESS_TOKEN_EXPIRE_MINUTES, default 720 min
}
```

JWT claims (типовые): `sub`=username, `uid`, `tv`=token_version, `jti`, `exp`; для stream-токенов дополнительно `scope=stream`.

> Ревью 2026-10-01: подпись/истёкший exp проверяются, но наличие exp/jti не обязательно; uid пока не сверяется с ID выбранного по username пользователя. Это открытые findings, не гарантии строгого контракта. Пароли bcrypt ограничены 72 UTF-8 байтами по поведению библиотеки, хотя схема допускает 128 символов. См. [[Ревью Auth 2026-10-01]].

---

## Поведение при неудачах

| Ситуация | HTTP | Клиент |
|----------|------|--------|
| Неверный пароль / нет юзера | 401 | AuthModal |
| Аккаунт locked_until | 401 | Ждать lockout |
| Истёкший/отозванный JWT | 401 | Event `tmusic:unauthorized` → AuthModal |
| Rate limit | 429 | `Retry-After` сек |

Frontend: `src/api/auth.js`, `src/store/authStore.js`.

---

## Bootstrap администратора

1. `music.env` → `ADMIN_PASSWORD_HASH` (bcrypt) применяется при каждом старте (`app/services/bootstrap.py`)
2. CLI: `python scripts/init_admin.py` / `--password "..."` / `--hash-only "..."` / `--clear-pin`
3. После первого успешного входа hash-строку в env можно удалить и менять пароль через API

Подробнее: [[Архитектура безопасности]] · [[music.env]]

---

## Связанные заметки

- [[Обзор Backend]]
- [[Модели БД]]
- [[Аудит 2026-09]]
- [[Архитектура безопасности]]

[[00-Главная]]
