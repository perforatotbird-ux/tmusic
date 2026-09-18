# API — Stream

#tmusic #backend #api #streaming

Prefix: `/api/stream` · Router: `backend/app/api/stream_router.py` · Service: `stream_service`

---

## POST `/api/stream/token`
| | |
|--|--|
| Auth | session JWT (cookie/Bearer) |
| Ответ | `{ stream_token, expires_in }` |
| TTL | **10 минут** (`STREAM_TOKEN_TTL`) |
| scope | `stream` — только для `?token=` |

Почему короткий: URL с query попадает в логи прокси, history, Referer — 12-часовой session JWT там не должен ездить.

---

## GET `/api/stream/tracks/{folder_id}`
| Query | Default | |
|-------|---------|--|
| recursive | true | Включая подпапки (multi-disc) |
| limit | — | 1–1000 |
| offset | 0 | |

→ `TrackResponse[]`:
```
{ id, name, size, mime_type, folder_id, stream_url, created_at }
```
- `size` — plaintext size (учёт AES-GCM overhead)
- `stream_url` — `/api/stream/{id}`
- Сортировка — natural sort

---

## GET `/api/stream/{file_id}`
Основной стриминговый эндпоинт.

| Аспект | Поведение |
|--------|-----------|
| Auth | session JWT **или** `?token=` (scope=stream) |
| Range | RFC 7233 → **206 Partial Content** |
| Decrypt | on-the-fly AES-256-GCM если `vfile.is_encrypted` |
| Cache | RAM-cache hit → без похода в Telegram |
| Miss | MTProto download через [[Telegram кластер]] |
| Failover | FloodWait → смена аккаунта mid-stream |
| 401 | Без валидной сессии/токена |

---

## Pre-cache (клиент)

В `App.jsx` / `playerStore`: при `currentTime/duration > 0.75` → `precacheNextTracks()`:
- следующие **2** трека
- первые **2 МБ** (`PRECACHE_CHUNK_BYTES`)
- abort предыдущих prefetch при новой очереди
- Set id ограничен ~500 записями

См. [[ADR-003 Streaming Cache]]

---

## Frontend

- `src/api/stream.js` — `getStreamUrl`, mint token
- `src/store/playerStore.js` — queue, shuffle, repeat, precache
- `src/services/mediaSession.js` — lock-screen controls

---

## Связанные заметки

- [[Кэш и стриминг]]
- [[Telegram кластер]]
- [[API Library]]
- [[Модели БД]]

[[00-Главная]]
