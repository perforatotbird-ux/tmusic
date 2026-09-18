# ADR-004 Cookie Auth CSRF

#tmusic #adr #security #frontend

| Поле | Значение |
|------|----------|
| Статус | **Accepted** |
| Дата | 2026-09 (закреплено hardening'ом) |
| Решение | HttpOnly session cookie + short stream token in query + double-submit CSRF |

---

## Контекст

- SPA same-origin на FastAPI (prod) / Vite proxy (dev)
- HTML5 `<audio>` не умеет слать Authorization
- Нельзя класть 12-часовой JWT в URL (логи, Referer, history)
- Нельзя хранить JWT в localStorage (XSS = полный доступ)

## Решение

### Session
1. `POST /login` → JWT
2. Set-Cookie:
   - `access_token` — **HttpOnly**, SameSite=Lax, Secure при HTTPS
   - `csrf_token` — readable, double-submit
3. API-запросы: `credentials: 'same-origin'`
4. Mutations: header `X-CSRF-Token` = cookie value
5. Middleware: cookie-auth без CSRF-token → **403**
6. Bearer-auth → CSRF exempt
7. Logout → revoke `jti` в БД

### Stream (special case)
1. `POST /api/stream/token` → JWT `scope=stream`, TTL **10 min**
2. `<audio src="/api/stream/{id}?token=...">`
3. Token не должен жить дольше сессии трека

### Client failure modes
| HTTP | Клиент |
|------|--------|
| 401 | `tmusic:unauthorized` → AuthModal |
| 429 | wait Retry-After |

## Альтернативы

| Вариант | Вердикт |
|---------|---------|
| JWT only in localStorage | Отклонён — XSS-совместимость |
| Bearer для всего UI | Неудобно для `<audio>` + больше JS state |
| Long-lived JWT в query | Отклонён — утечки в логи |
| SameSite=Strict | Возможны edge-case с внешними редиректами; выбран Lax + CSRF |

## Связь

- [[API Auth]]
- [[API Stream]]
- [[Архитектура безопасности]]
- [[Обзор Frontend]]
- [[Zustand stores]]

---

[[00-Главная]]
