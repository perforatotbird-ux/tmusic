# ADR-002 Fernet TG Sessions

#tmusic #adr #security #telegram

| Поле | Значение |
|------|----------|
| Статус | **Accepted** |
| Дата | 2026-09-15 (усилено после аудита) |
| Решение | Хранить Telegram credentials в PostgreSQL только в Fernet-шифрованном виде |

---

## Контекст

`tg_accounts.session_string` и `api_hash` — полноценный доступ к Telegram-аккаунтам. Открытый текст в БД = захват кластера при любой SQL-инъекции / бэкап-утечке / path traversal к дампу.

Секрет шифрования — `SESSION_SECRET` из `music.env`.

## Решение

```
Fernet key = urlsafe_b64(sha256(SESSION_SECRET))
stored = Fernet(key).encrypt(plain)
```

Инварианты:
1. **At-rest всегда encrypted** при наличии `SESSION_SECRET`
2. Decrypt **только in-memory** в `TelegramManager.start()`
3. **Self-heal** plaintext → encrypted при старте
4. Единый helper `ensure_encrypted` / `seal_tg_account` для runtime, CLI, миграции
5. Миграция из CloudByte шифрует при копировании
6. Офлайн-аудит: `scripts/secure_tg_accounts.py --check`

## Альтернативы (отклонены)

| Вариант | Почему нет |
|---------|------------|
| Plaintext в env только | Не масштабируется на multi-account кластер |
| Plaintext в БД | Слишком ценный актив |
| Шифрование на уровне диска (LUKS) | Не помогает при SQL-доступе/бэкапах на другом хосте |
| Сразу ротировать SESSION_SECRET после аудита | Сделал бы все текущие сессии нечитаемыми |

## Риски

| Риск | Митигация |
|------|-----------|
| Потеря SESSION_SECRET | Backup; ротация = перевыпуск сессий через `create_session.py` |
| SESSION_SECRET утёк вместе с music.env | Аудит C-1 закрыт; ротировать SECRET_KEY уже сделано; SESSION_SECRET ротировать отдельным планом |
| Путь без SESSION_SECRET | `encrypt` no-op, plaintext остаётся — логировать и чинить env |

## Связь

- [[Telegram кластер]]
- [[Архитектура безопасности]]
- [[Устранение уязвимостей]]
- [[music.env]]

---

[[00-Главная]]
