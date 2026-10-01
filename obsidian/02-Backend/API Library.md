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
Secure proxy обложки (по key или url).
- **401** без auth
- SSRF-фильтр хостов (`is_safe_host`)
- Timeout: `COVER_HTTP_TIMEOUT_SEC` / `COVER_HTTP_CONNECT_TIMEOUT_SEC`
- Max bytes: `COVER_MAX_IMAGE_BYTES` (10 МБ)

### Upload / set cover
`SetCoverRequest`, multipart upload; hard cap `MAX_UPLOAD_BYTES = MAX_IMAGE_BYTES + 1`.
- Magic bytes: PNG, JPEG, WebP, GIF
- **SVG запрещён** — Stored XSS
- Лимит 10 МБ

Подробнее: [[Архитектура безопасности]] · [[Кэш и стриминг]]

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
- `POST /api/playback/history`: played и track duration — finite 0..86400 секунд; invalid input → 422, запись не создаётся. Optional track duration допускает None.
- Старые invalid history durations санитайзятся в ответах (played=0, track=None), admin analytics исключает invalid seconds до суммирования. Это read-only совместимость, не массовая очистка БД.

Результаты: [[Ревью граничных значений 2026-10-01]] · [[Тестирование]].

[[00-Главная]]

## Async: batch mirror

POST `/api/playlists/mirror` (auth): `{name, file_ids}`. Уникальные положительные ID, максимум 5000. Find/create + полная ordered замена атомарны; invalid/missing/trashed ID не меняет старый список. Пустой массив явно очищает список. FolderBrowser отправляет максимум 500 tracks, как раньше. См. [[Ревью async 2026-10-01]].

## Atomic playlist creation

POST `/api/playlists` принимает name и optional `initial_file_id` (positive strict integer). Track validate + playlist + first item выполняются одним commit; missing/trash track или commit failure откатывают создание. Ответ PlaylistSummary имеет track_count=1 при initial track, иначе 0. Старый create(name) совместим. AlbumDetail передаёт track в том же POST, не делает второй addTrack. При network/5xx/malformed success результат неизвестен: ввод сохраняется, создание блокируется до обновления/проверки списка; blind retry/компенсирующее удаление не выполняются.
