# Структура UI

#tmusic #frontend #ui

---

## Экраны / компоненты

| Компонент | Роль |
|-----------|------|
| **MusicLibrary** | Корневая витрина: артисты, фильтр kind, поиск, карточки |
| **AlbumDetail** | Дискография/альбом: треклист, play all |
| **MusicLibraryPlayer** | Постоянный нижний плеер: play/pause, progress, volume, shuffle/repeat, expand |
| **AuthModal** | Login username+password (PIN нет) |
| **CoverPicker** | Выбор/загрузка обложки (custom cover) |

---

## Типичный user flow

```mermaid
flowchart TD
  Boot[App mount] --> Me[/api/auth/me/]
  Me -->|401| Auth[AuthModal]
  Me -->|200| Lib[MusicLibrary]
  Auth -->|login OK| Lib
  Lib --> Artist[Карточка артиста]
  Artist --> Album[AlbumDetail]
  Album --> Tracks[/api/stream/tracks/{folder_id}/]
  Tracks --> Play[playerStore.playQueue]
  Play --> Audio["<audio src=/api/stream/id>"]
  Audio --> PlayerBar[MusicLibraryPlayer]
  PlayerBar -->|75%| Precache[precache next]
  Audio --> MS[Media Session]
```

---

## Стилизация

- `index.css` + CSS-переменные (`--bg`, `--ink`)
- Утилиты: `clsx`, `tailwind-merge`
- Иконки: `lucide-react`

---

## Состояния UI

| Состояние | Источник |
|-----------|----------|
| Авторизован? | `authStore` + cookie |
| Что играет | `playerStore` |
| Модалка обложки | local state App (`coverTarget`) |
| Unauthorized banner/modal | window event `tmusic:unauthorized` |

---

## Связанные заметки

- [[Обзор Frontend]]
- [[Zustand stores]]
- [[API Library]]

[[00-Главная]]
