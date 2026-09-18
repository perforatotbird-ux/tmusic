# ADR-001 No God Objects

#tmusic #adr #архитектура

| Поле | Значение |
|------|----------|
| Статус | **Accepted** |
| Дата | проектная конвенция (отражена в README) |
| Решение | Чистая слоистая архитектура backend |

---

## Контекст

T-Music выделен из монолита CloudByte. Монолит склонен к god-объектам: один модуль тянет HTTP, Telegram, кэш, auth и БД.

## Решение

Разделить `backend/app/` на жёсткие слои:

```
api/           → только HTTP-контракт
services/      → бизнес-правила
repositories/  → SQL/ORM доступ
models/        → schema entities
tg/            → Telegram transport
core/          → config, security, infra primitives
schemas/       → pydantic DTO
utils/         → чистые helpers
```

Правила:
- Router **не** пишет SQL и **не** держит Telegram-клиенты
- Service **не** знает про FastAPI Request
- TG-manager — единственная точка MTProto
- Секреты и crypto — только в `core` / `tg` helpers

## Следствия

### Положительные
- Тестируемость (unit без HTTP/TG)
- Возможность заменить transport Telegram
- Меньше merge-конфликтов и blast radius

### Отрицательные
- Чуть больше boilerplate (repo/service/router)
- Нужна дисциплина на review — «просто вот так быстрее» ломает слой

## Связь с кодом

- [[Обзор Backend]]
- [[Архитектура]]
- [[Карта репозитория]]

---

[[00-Главная]]
