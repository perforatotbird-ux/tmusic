# API — Library

#tmusic #backend #api

Prefix: `/api/library` · Router: `backend/app/api/library_router.py` · Service: `music_library_service`

**Все эндпоинты требуют аутентификации** (`get_current_user`), включая cover-прокси.

---

## Catalog

### GET `/api/library/artists`
| Query | Default | Описание |
|-------|---------|----------|
| kind | — | `artist` / `compilation` / `standalone_album` / `genre_collection` |
| search | — | Поиск по display/search name |
| limit | 200 | 1–500 |
| offset | 0 | Пагинация |

→ `MusicArtistCard[]`

### GET `/api/library/albums`
| Query | Default |
|-------|---------|
| search | — |
| limit | 100 (1–500) |
| offset | 0 |

→ `MusicAlbumCard[]`

### GET `/api/library/artists/{artist_id}`
→ `MusicArtistDetail` — карточка артиста, альбомы, треки, bio.

### GET `/api/library/stats`
→ `LibraryStats` — агрегаты каталога.

---

## Covers

### GET `/api/library/covers/manifest`
Манифест ключей custom-cover. Charset: `^[A-Za-z0-9._-]{1,64}$`, макс. **500** ключей.

### GET `/api/library/cover`
Secure proxy обложки. Контракт отображения (2026-10-03): карточки
артистов/альбомов/поиска отдают **только** `?key=` (ключ `cover_cache.url_to_key`
от хранящегося remote URL) — показ всегда читает локальный дисковый кеш
(`cache/music_images/{key}.bin`) и никогда не ходит в сеть за remote URL.
Ветка `?url=` осталась как явный fetch для legacy-клиентов и разового
прогрева: сначала проверяет диск, качает только при промахе.
- **401** без auth
- SSRF-фильтр хостов (`is_safe_host`), pinned-resolver против DNS-rebinding
- Timeout: `COVER_HTTP_TIMEOUT_SEC` / `COVER_HTTP_CONNECT_TIMEOUT_SEC`
- Max bytes: `COVER_MAX_IMAGE_BYTES` (10 МБ)
- Прогрев кеша: `POST /api/admin/catalog/artists|albums/{id}/scan` и
  `POST /api/metadata/artists/{id}/enrich` скачивают картинку один раз
  (`fetch_and_cache`, best-effort); бэкфилл существующих строк —
  `python scripts/warm_cover_cache.py [--dry-run] [--limit N]` (БД не пишет).
  Сканирование прогревает кеш только тогда, когда реально выбрало новую картинку
  (обложка из Last.fm или из альбома) — см. [[Сканирование каталога]].
- Разовый ремонт строк с placeholder-обложкой Last.fm:
  `python scripts/repair_placeholder_covers.py [--apply] [--albums-per-artist N]`
  (dry-run по умолчанию, пишет JSON-бэкап в `cache/`) — см. [[Сканирование каталога]].

### Upload / set cover
`SetCoverRequest`, multipart upload; hard cap `MAX_UPLOAD_BYTES = MAX_IMAGE_BYTES + 1`.
- Magic bytes: PNG, JPEG, WebP, GIF
- **SVG запрещён** — Stored XSS
- Лимит 10 МБ

### Три уровня обложек: `artist` / `album` / `track`
`POST /api/library/cover` (multipart `target_type`, `target_id`, `file` | `url`) и
`DELETE /api/library/cover` (query `target_type`, `target_id`) принимают три
значения `target_type`. Все уровни хранят ключ в одном и том же поле
`custom_image_key` и обслуживаются одним кешем `cover_cache`:

| target_type | Модель | Поле |
|-------------|--------|------|
| `artist` | `MusicArtist` | `custom_image_key` |
| `album` | `MusicAlbum` | `custom_image_key` |
| `track` | `VFile` | `custom_image_key` (колонка добавлена 2026-10-12) |

Иное значение → **400**, отсутствующая сущность → **404**. Диспетчер —
`MusicLibraryService.set_custom_cover()`; правка только для админа (`get_admin_user`).

**Обложки не наследуются по иерархии.** Обложка артиста никогда не подставляется
альбомам (баг: ручная обложка артиста протекала во все его альбомы). Альбом без
своей обложки показывает плейсхолдер, а не картинку артиста. Для трека
наследование альбомной обложки сохранено: своя обложка трека **приоритетнее**
(`search_repository` → `t.custom_image_key or meta.custom_image_key`).

Клиентский резолвер — `frontend/src/utils/cover.js`; `resolveCoverUrl(entity, fallback)`
не принимает сущность-родителя и игнорирует не-строковый `fallback`.

Подробнее: [[Архитектура безопасности]] · [[Кэш и стриминг]]

---

## Admin Catalog — сканирование

Prefix: `/api/admin/catalog` · Router: `backend/app/api/admin_catalog_router.py` ·
Admin-only (`get_catalog_user`).

### POST `/api/admin/catalog/artists/{artist_id}/scan`
Обновляет био, MBID и обложку артиста из Last.fm и добивает отсутствующие обложки
его альбомов (не более `MAX_ALBUMS_PER_SCAN = 12` запросов к Last.fm за прогон).
Запросы идут с `force=True` — мимо 14-дневного кеша.

**Это фоновая задача.** POST отвечает сразу (`{accepted: true, job_id, status:
"running", progress: {...}}`) и **не** содержит результат скана — обновление
десятки секунд идёт в `scan_service`, а прогресс читается отдельным GET. Иначе
кнопка выглядела мёртвой: ни счётчика, ни этапа, ни ошибки.

Правила:
- альбомы с `custom_image_key` пропускаются — ручная обложка неприкосновенна;
- `image_url` артиста перезаписывается только если текущей нет или она
  placeholder (`2a96cbd8…`) — ничего не понижается в качестве;
- пустое био и стаб «Read more on Last.fm» не записываются;
- при изменениях `locked = True` (явный скан = курирование).

Ответ: `{ok, id, display_name, updated, fields, albums_scanned, albums_updated,
albums_total, image_source, bio, image_url, mbid, tags, similar}`, где
`image_source ∈ lastfm | album | existing | None`.

- **404** — артист не найден локально или на Last.fm
- **409** — этот артист уже сканируется, либо выполняется `MAX_CONCURRENT_SCANS = 3`
- **503** — Last.fm не сконфигурирован (`metadata_ready()`)

Результат скана приходит в поле `report` снапшота задачи, см. ниже.

### GET `/api/admin/catalog/scan/{job_id}`
Снапшот фоновой задачи:
`{job_id, artist_id, display_name, status ∈ running|completed|failed, error,
report, progress: {current, total, stage, message}}`.
Этапы: `artist` → `albums` (по шагу на альбом) → `save` → `done` (`failed` при
ошибке). Задача видна **только своему** актору: чужой `job_id` даёт **404**.
Фронтенд опрашивает её через `startSerialPolling` и показывает бар в левом
верхнем углу ([[Курирование обложек и имён]]).

### POST `/api/admin/catalog/albums/{album_id}/scan`
`album.getInfo` → при промахе `track.getInfo` по первому треку альбома. Обновляет
`mbid` и `cover_url` (только если нет `custom_image_key`), прогревает кеш.
Ответ: `{ok, id, title, updated, fields, mbid, cover_url}`. Те же **404** / **503**.

Подробнее о причинах и guard-ах: [[Сканирование каталога]]

---

## Связь с плеером

1. UI открывает артиста/альбом → `library` API
2. Список треков альбома — через **[[API Stream]]** `GET /api/stream/tracks/{folder_id}`
3. Клик play → `playerStore.playQueue` → `/api/stream/{file_id}`

---

## Frontend клиенты

- `src/api/library.js`
- `src/components/MusicLibrary.jsx`
- `src/components/AlbumDetail.jsx`
- `src/components/CoverPicker.jsx`

---

## Связанные заметки

- [[API Stream]]
- [[Модели БД]]
- [[Обзор Backend]]

## Плейлисты, lyrics и история — граничные значения

- `POST /api/playlists`: trim имени до min_length, 1–120 символов; whitespace-only input → 422. Service layer отдельно валидирует имя, Subsonic create/update использует protocol error 10 для некорректного имени.
- LRCLIB: null/non-dict search entries игнорируются до score/sort, строковые поля проверяются перед strip/lower, nonfinite/negative duration → None. Network errors остаются best-effort.
- `POST /api/playback/history`: played и track duration — finite 0..86400 секунд; invalid input → 422, запись не создаётся. При передаче `history_id` запись обновляется на месте без дублирования.
- Старые invalid history durations санитайзятся в ответах (played=0, track=None), admin analytics исключает invalid seconds до суммирования и выдаёт ленту последних событий (`recent`). Это read-only совместимость, не массовая очистка БД.

Результаты: [[Ревью граничных значений 2026-10-01]] · [[Тестирование]] · [[Исправление аналитики и трекинга прослушиваний 2026-10-13]].

[[00-Главная]]

## Async: batch mirror

POST `/api/playlists/mirror` (auth): `{name, file_ids}`. Уникальные положительные ID, максимум 5000. Find/create + полная ordered замена атомарны; invalid/missing/trashed ID не меняет старый список. Пустой массив явно очищает список. FolderBrowser отправляет максимум 500 tracks, как раньше. См. [[Ревью async 2026-10-01]].

## Atomic playlist creation

POST `/api/playlists` принимает name и optional `initial_file_id` (positive strict integer). Track validate + playlist + first item выполняются одним commit; missing/trash track или commit failure откатывают создание. Ответ PlaylistSummary имеет track_count=1 при initial track, иначе 0. Старый create(name) совместим. AlbumDetail передаёт track в том же POST, не делает второй addTrack. При network/5xx/malformed success результат неизвестен: ввод сохраняется, создание блокируется до обновления/проверки списка; blind retry/компенсирующее удаление не выполняются.

## Переименование плейлиста

`PATCH /api/playlists/{playlist_id}` (auth) — тело `{name}`, схема `PlaylistName`
(trim, 1–120 символов; пустое/whitespace-only → 422). Переименовывает только плейлист
текущего пользователя; чужой или несуществующий → 404. Ответ — `PlaylistSummary`,
список треков не затрагивается.

Реализация: `playlist_service.rename_playlist()` — валидация и границы транзакции в
сервисе, выборка через `PlaylistRepository.get(..., for_update=True)`. Используется
окном [[Окно плейлистов]]. См. [[Аудит маршрутизации API 2026-10-11]] (IDOR-контроль:
принадлежность плейлиста проверяется по `user_id`).
