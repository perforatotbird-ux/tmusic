# Обзор: устранение уязвимостей T-Music (2026-09-15)

Все рекомендации аудита OWASP Top 10 (`.sec_audit/SECURITY_AUDIT.md`) выполнены.

## Сделано
- **C-1 (path traversal в SPA):** исправлен в `backend/app/main.py` (resolve + is_relative_to); PoC повторно прогнан на живом сервере — секреты больше не отдаются.
- **C-2 (дефолтные креды):** удалены из миграции; bootstrap `app/services/bootstrap.py` применяет bcrypt-хэш из `ADMIN_PASSWORD_HASH`; CLI `scripts/init_admin.py`; эндпоинт `POST /api/auth/change-password`.
- **C-3 (секреты):** `SECRET_KEY` ротирован; пароль панели установлен (bcrypt в `music.env`).
- **TG-ключи в БД зашифрованы** (Fernet от `SESSION_SECRET`): self-heal при старте + `scripts/secure_tg_accounts.py` + шифрование в миграции.
- PyJWT вместо python-jose; несолёный SHA-256 отклоняется; rate-limit API (120/мин, auth 10/мин); cover-прокси закрыт авторизацией; логирование auth-событий; LIKE-escape; RAM-кэш с байтовым лимитом; Docker non-root + HEALTHCHECK + no-new-privileges; зависимости запинены; тесты обновлены.

## Проверка
11/11 юнит-тестов зелёные; крипто-раундтрипы и bootstrap (3 кейса) ОК; живой сервер: traversal закрыт, 429 после 10 запросов, 401 на закрытых API.

## Примечания
- PostgreSQL/Docker в среде выключены → применение к живой БД произойдёт автоматически при первом старте (bootstrap + self-heal шифрования).
- `SESSION_SECRET` не ротирован (на нём шифрование TG-сессий); ротация = перевыпуск сессий.

Подробности: `.sec_audit/REMEDIATION_REPORT.md`
