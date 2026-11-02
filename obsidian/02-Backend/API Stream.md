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
| Auth | session JWT в cookie/header **или** `?token=` со `scope=stream` |
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

## Logic fixes: representation и encrypted size

Main/share/Subsonic используют один response builder, выбирающий raw или cached MP3 до headers. MIME, filename, total size и Range всегда от выбранного representation; появление cache после выбора не подменяет source. Для encrypted raw сначала читается/caches 32-byte header, расчёт по реальному block_size. Если header недоступен — 503 до response body. В track cards size может быть null до первого header resolution; default 1 MiB не используется как точный размер. См. [[Ревью бизнес-логики 2026-10-01]].

## Error-handling preflight

Main/share/Subsonic builder получает первый непустой chunk до response.start (timeout 30 секунд, не буферизация всего трека). Upstream failure/empty body -> безопасный 503 до headers. После headers неожиданный EOF/exception не заменяется на HTTPException: body прерывается, structured server log фиксирует track_id/remaining bytes; audio onError в App показывает notice/останавливает или пропускает track. Generator/source закрывается при completion/failure/cancellation/disconnect. Content-Length/Range остаются привязаны к выбранной representation. Живой Telegram/proxy/device тест ещё не выполнен.

## Явное MP3-представление (2026-10-08)

`GET /api/stream/{file_id}?representation=mp3` требует готовый кэш для форматов с `needs_transcoding`; если его нет — HTTP 409 + Retry-After: 2. Для native-compatible файлов сохраняется обычный stream. Auth/Range остаются прежними; ответ явного MP3 использует `private, no-store`. Web-player вызывает POST transcode и опрашивает transcode-status до готовности, отменяя ожидание при смене трека. См. [[Кэш и стриминг]].

`transcode-status` также возвращает `job_active`: pending без активной задачи (например, после перезапуска процесса) показывает retryable error вместо бесконечного ожидания. Эвиктированный READY-кэш считается FAILED и допускает повторный POST. Browser prep проверяется polling, SSE остаётся каналом уведомлений, а не единственным условием готовности.

## Блокирующая работа вне событийного цикла (2026-10-21)

Рывки/задержки при shuffle оказались не проблемой Range/префетча, а остановками
единственного event loop: Last.fm вызывался блокирующим `urllib` прямо в
`POST /api/playback/history` (до 2.1 с на каждой смене трека), `covers/manifest`
делал до 500×4 `stat()` в цикле (до 5.1 с), а `get_status/get_result` могли
перестраивать индекс манифестов. Исправлено: Last.fm — через `BackgroundTasks`,
файловые обходы и транскодные проверки — через `run_in_threadpool`, position-ручки
переведены в sync `def`. См. [[Задержка воспроизведения 2026-10-21]].

## Точный расчет размера зашифрованных треков (2026-10-09)

В `stream_service.py` и `transcoding_service.py` исправлен расчет размера для зашифрованных треков (`is_encrypted=True`). В базе данных `vfiles.size` хранит размер исходного (plaintext) файла. Для предотвращения усечения хвостовых метаданных (атомы `moov`/`stsz` в M4A) размер скачиваемого исходника теперь сопоставляется с медиа-размером Telegram через `FastStreamer.get_source_media_size` и кэшируется в `_plain_size_cache`. См. подробности в [[Кэш и стриминг]].

## Заголовки кэширования и мгновенная нотификация готовности (2026-10-09)

Для эндпоинтов `GET /api/stream/{file_id}/transcode-status` и `POST /api/stream/{file_id}/transcode` добавлены строгие заголовки запрета кэширования ответов (`Cache-Control: no-cache, no-store, must-revalidate`, `Pragma: no-cache`). Во фронтенде ожидание готовности разрешается моментально при получении SSE-события `transcode_complete` через глобальное событие `tmusic:transcode-complete` без задержек полинга. Во время ожидания на `<audio>` удерживается воспроизведение минимального неслышимого WAV (`SILENT_AUDIO_URI`), сохраняя разрешение браузера на автозапуск при подмене потока на MP3.


