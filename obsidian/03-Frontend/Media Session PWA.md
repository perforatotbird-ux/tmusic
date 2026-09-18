# Media Session & PWA

#tmusic #frontend #pwa

Файлы:
- `frontend/src/services/mediaSession.js`
- `frontend/public/manifest.json`
- `frontend/public/icon.svg`
- `App.jsx` — вызовы `setupMediaSession`, `updateMediaSessionMetadata`, `updateMediaSessionPosition`

---

## Media Session API

Цель: нативное управление треком **без открытия вкладки** — lock-screen, Bluetooth-гарнитуры, автомобильные головные устройства.

| Метод модуля | Что делает |
|--------------|------------|
| `setupMediaSession(usePlayerStore)` | Регистрирует action handlers (play, pause, previoustrack, nexttrack, seekto…) |
| `updateMediaSessionMetadata(track, release)` | MediaMetadata: title, artist, album, artwork (cover) |
| `updateMediaSessionPosition(duration, current)` | Position state — прогресс на lock-screen |

Обновление метаданных происходит при смене `currentTrack?.id` в `App.jsx`.

---

## PWA manifest

```json
{
  "name": "T-Music",
  "start_url": "/",
  "display": "standalone",
  "icons": [{ "src": "icon.svg", "sizes": "any", "type": "image/svg+xml" }]
}
```

(Точное содержимое — в `frontend/public/manifest.json`.)

Установка: Add to Home Screen на iOS/Android → standalone-режим.

---

## Ограничения / заметки

- Autoplay-политика браузеров: первый play может требовать user gesture
- `setActionHandler` должен быть переподключён, если стор размонтирован
- Обложки для Media Session должны быть доступны по HTTPS/относительному URL (cover proxy auth — cookie same-origin)

---

## Связанные заметки

- [[Обзор Frontend]]
- [[Zustand stores]]
- [[API Stream]]

[[00-Главная]]
