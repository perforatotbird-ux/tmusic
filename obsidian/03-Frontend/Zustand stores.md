# Zustand stores

#tmusic #frontend #state

Библиотека: **Zustand 5**. Два корневых стора.

---

## useAuthStore (`store/authStore.js`)

| Состояние / метод | Роль |
|-------------------|------|
| `checkAuth()` | `GET /api/auth/me` при boot |
| `setAuthModalOpen(bool)` | Управление AuthModal |
| login/logout | Обёртки над `api/auth.js` |

Auth-флоу: любой 401 → global event → AuthModal.

---

## usePlayerStore (`store/playerStore.js`)

### State
| Поле | Тип | Примечание |
|------|-----|------------|
| currentTrack | object? | id, name, mime… |
| currentRelease | object? | `{ title, artistName, coverUrl }` |
| queue | Track[] | |
| queueIndex | number | -1 пусто |
| isPlaying | bool | |
| currentTime / duration | number | синхронизируется из `<audio>` |
| volume | number | default 0.9; persist `localStorage.tmusic_volume` |
| isMuted | bool | |
| shuffle | bool | |
| repeat | `'off' \| 'all' \| 'one'` | |
| isPlayerExpanded | bool | UI |
| audioElement | HTMLAudioElement? | set из App.jsx |

### Ключевые actions
| Action | Поведение |
|--------|-----------|
| `playTrack(track, releaseInfo)` | Один трек / позиция в queue |
| `playQueue(tracks, startIndex, releaseInfo)` | Новая очередь; **abort** всех in-flight prefetch |
| `togglePlay` | play/pause через audioElement |
| `nextTrack` / prev | shuffle / repeat-логика |
| `precacheNextTracks` | fetch 2MB next×2, Set кэш id (cap ~500), AbortController |
| `setAudioElement` | Привязка DOM |

### Prefetch details
```
precachedTracks: Set        // bounded ~500
prefetchAborts: Map         // trackId → AbortController
PRECACHE: 2 tracks × 2MB
Trigger: currentTime/duration > 0.75
```

---

## Шаблон использования

```js
const { playQueue, togglePlay } = usePlayerStore()
playQueue(albumTracks, 2, { title, artistName, coverUrl })
```

Stream URL строится в `api/stream.js` (`getStreamUrl(id)`), при cookie-auth без query; при необходимости mint token.

---

## Связанные заметки

- [[Обзор Frontend]]
- [[API Stream]]
- [[Кэш и стриминг]]
- [[Media Session PWA]]

[[00-Главная]]
