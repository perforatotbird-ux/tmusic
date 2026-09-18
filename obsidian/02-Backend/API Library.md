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

[[00-Главная]]
