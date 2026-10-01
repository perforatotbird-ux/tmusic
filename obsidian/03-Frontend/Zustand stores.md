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
| `playNext(track)` | Вставляет после currentTrack; индекс playing track пересчитывается по ID, shuffle тоже играет scheduled track следующим; current track → no-op |

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

## Async lifecycle

Auth check/login/logout используют session epoch; cookie-mutating login/logout сериализованы. clearLocalSession сбрасывает player/favorites и invalidates cached API requests; поздний /me не восстанавливает logout-сессию. Favorites toggle сериализован по ID, rollback меняет только этот ID; load/reset защищены epoch/revision. Resume позиции сверяет generation и текущий трек. См. [[Ревью async 2026-10-01]].

## Logic fixes

Shuffle removal сохраняет позицию текущего трека в перестановке; mute независим от volume и восстанавливает последнее audible значение, audio.muted синхронизируется. Browser SSE использует существующий store вместо CommonJS require. Metadata update не меняет playback state; native cover URL абсолютный HTTP(S), native fetch использует scoped WebView cookie без redirects. См. [[Ревью бизнес-логики 2026-10-01]].

## Error-handling outcomes

- auth.logout -> {ok,code,message}; logoutPending/logoutBusy, safe warning/retry в AuthModal. Tab sessionStorage содержит только pending boolean; после неизвестного результата auth/me/login не выполняются до retry logout.
- favorites.toggle -> {ok,isFavorite,code,message}, не ambiguous boolean. Rollback только текущего ID сохранён, invalid response тоже failure. Store error показан App независимо от наличия player; refresh сверяет server state и очищает warning, reset очищает ошибки.
- API client ApiError хранит status/code/нормализованные validation issues (без input/ctx), retryAfter. AbortError остаётся cancellation; network/5xx generic, 4xx detail.message/validation читаемы.
- Album picker/playlist panel показывают mutation errors, не только console. Admin allSettled помечает per-section loading/error/stale; sequence защищает новый refresh от старого ответа.
- Проверки: 72 Node tests, production build и два mocked-API UI smoke. См. [[Ревью обработки ошибок 2026-10-01]].
