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
| 200 | `TokenResponse` — JWT + `expires_in` + `password_change_required` |
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
- 200: `{ id, username, is_active, role, avatar_url, password_change_required }`
- 401: нет/невалиден JWT

### POST `/api/auth/change-password`
- Body: `{ current_password, new_password }`
- Требует корректный текущий пароль; новый — **8–128 символов**; текущий и login допускают до 1024 символов для legacy-паролей
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
  "expires_in": 43200,  // ACCESS_TOKEN_EXPIRE_MINUTES, default 720 min
  "password_change_required": false
}
```

JWT claims (обязательные): `sub`=username, `uid`, `tv`=token_version, `jti`, `iat`, `exp`; для stream-токенов дополнительно `scope=stream`. Проверяются типы, подпись, срок, uid=user.id и tv. Ограниченная сессия смены пароля дополнительно содержит `pwd_reset=true`.

> AUTH-01/02/03 исправлены локально в `argon2idmigration`: новые хэши Argon2id, строгий JWT и привязка uid. CORS намеренно не изменён. См. [[Ревью Auth 2026-10-01]] и [[ADR-005 Argon2id Migration]].

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

1. `music.env` → `ADMIN_PASSWORD_HASH` (Argon2id или legacy bcrypt) применяется однократно для данного значения; изменение значения инициирует явную ротацию при старте (`app/services/bootstrap.py`)
2. CLI: `python scripts/init_admin.py` / `--password "..."` / `--hash-only "..."`
3. После первого успешного входа hash-строку в env можно удалить и менять пароль через API

Подробнее: [[Архитектура безопасности]] · [[music.env]]

---

## Связанные заметки

- [[Обзор Backend]]
- [[Модели БД]]
- [[Аудит 2026-09]]
- [[Архитектура безопасности]]

## Миграция паролей (2026-10-01)

- Новый пароль, admin create/reset и CLI: Argon2id; длина PHC проверена для VARCHAR(255).
- Короткий bcrypt (<72 UTF-8 байт введённого пароля) и salted SHA-256 обновляются после успешного входа; token_version не меняется при rehash того же пароля.
- Для bcrypt при вводе >=72 байт выдаётся сессия до 10 минут с `password_change_required=true`. Доступны только `/api/auth/me`, `/api/auth/change-password`, `/api/auth/logout`; остальные auth-guarded API отвечают 403. Суффикс не закрепляется автоматически.
- Смена пароля проверяет актуальный DB-хэш под row lock; token_version увеличивается. UI очищает локальную сессию и просит повторный вход.
- Выделенные Subsonic-токены не мигрируются: это отдельный механизм.

## Создание пользователя администратором

`POST /api/admin/users`: username нормализуется strip до проверки длины 2–64 и unique lookup. Whitespace-only input → 422, существующий нормализованный username → 400. При concurrent unique conflict выполняется rollback и возвращается generic 400, без SQL details. Пароль не trim: он сохраняется как введён с Argon2id. См. [[Ревью граничных значений 2026-10-01]].

[[00-Главная]]

## Неподтверждённый logout

HTTP endpoint сохраняет revoke-before-cookie-clear: при DB/server failure успешный отзыв не объявляется. Frontend logout возвращает явный {ok,code}, сразу очищает local player/auth/cache, но при API failure показывает предупреждение и retry. Boolean pending marker (без token) сохраняется в sessionStorage best-effort; checkAuth/login не восстановят сессию до успешного retry. HttpOnly cookie удаляется серверным response, не JavaScript. Это fail-closed UI, не замена server revocation. См. [[Ревью обработки ошибок 2026-10-01]].
