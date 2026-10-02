# API — Search

#tmusic #backend #api #search #performance

Prefix: `/api/search` · Router: `backend/app/api/search_router.py` · Service: `app/services/search_service.py` · Repository: `app/repositories/search_repository.py`

Быстрый полнотекстовый поиск по 87 000+ аудиофайлам, альбомам и исполнителям. Архитектура строго разделена по слоям согласно [[ADR-001 No God Objects]].

Все эндпоинты требуют аутентификации (`get_current_user`).

---

## Архитектура и оптимизация производительности

1. **GIN-триграмм индексы (PostgreSQL)**:
   - `music_artists`: `idx_artists_name_trgm` на `lower(display_name)`
   - `music_albums`: `idx_albums_title_trgm` на `lower(title)`
   - `vfiles`: частичный индекс `idx_vfiles_name_trgm` на `lower(name)` с условием `WHERE is_trash = false AND mime_type LIKE 'audio/%'`
   - Обеспечивает выполнение поиска по 87 000+ трекам за **1.5–5 мс**.
2. **Двухшаговый пакетный поиск (без N+1 и без декартова умножения)**:
   - Шаг 1: Быстрый отбор треков из `vfiles` по имени с релевантным ранжированием (точное совпадение > префиксное совпадение > алфавитный порядок).
   - Шаг 2: Единый пакетный запрос через `folder_closures` (`ORDER BY depth ASC`) для привязки ближайшего альбома и артиста ко всем полученным `folder_id`.
3. **In-Memory LRU-кэш**:
   - `_SearchLRUCache` на 256 записей с TTL 30 секунд для моментального ответа на повторяющиеся запросы при вводе.

---

## Эндпоинты

### GET `/api/search`
Сводный сквозной поиск по всем категориям за один сетевой запрос.

| Параметр | Тип | По умолчанию | Описание |
|---|---|---|---|
| `q` | `string` | — | Поисковый запрос (макс. 200 символов) |
| `limit_artists` | `int` | `6` | Максимум исполнителей в ответе (1–50) |
| `limit_albums` | `int` | `8` | Максимум альбомов в ответе (1–50) |
| `limit_tracks` | `int` | `15` | Максимум треков в ответе (1–100) |

**Ответ (`SearchUnifiedResponse`):**
```json
{
  "query": "metallica",
  "artists": [
    {
      "id": 50,
      "display_name": "Metallica",
      "kind": "artist",
      "image_url": "https://...",
      "custom_image_url": null,
      "album_count": 20,
      "folder_id": 3070
    }
  ],
  "albums": [
    {
      "id": 396,
      "title": "2003. St. Anger [SICP 373~4]",
      "year": 2003,
      "artist_id": 50,
      "artist_name": "Metallica",
      "cover_url": "https://...",
      "custom_image_url": null,
      "folder_id": 3074
    }
  ],
  "tracks": [
    {
      "id": 54129,
      "title": "Frantic",
      "file_name": "01. Frantic.mp3",
      "size": 14249031,
      "duration": 350.4,
      "stream_url": "/api/stream/54129",
      "folder_id": 3074,
      "album_id": 396,
      "album_title": "2003. St. Anger [SICP 373~4]",
      "artist_id": 50,
      "artist_name": "Metallica",
      "cover_url": "https://...",
      "custom_image_url": null
    }
  ],
  "total_artists": 1,
  "total_albums": 20,
  "total_tracks": 240
}
```

---

### GET `/api/search/tracks`
Постраничный поиск по трекам.

| Параметр | По умолчанию | Описание |
|---|---|---|
| `q` | — | Поисковый запрос |
| `limit` | `30` | Размер страницы (1–100) |
| `offset` | `0` | Смещение пагинации |

→ `SearchPaginatedTracks` (`{ query, items, total, limit, offset }`)

---

### GET `/api/search/artists`
Постраничный поиск по артистам.

→ `SearchPaginatedArtists` (`{ query, items, total, limit, offset }`)

---

### GET `/api/search/albums`
Постраничный поиск по альбомам с привязкой артиста.

→ `SearchPaginatedAlbums` (`{ query, items, total, limit, offset }`)

---

## Связанные компоненты и заметки

- [[ADR-001 No God Objects]]
- [[API Library]]
- [[API Stream]]
- [[00-Главная]]
