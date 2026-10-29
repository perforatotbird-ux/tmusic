# Frontend — обзор

#tmusic #frontend

SPA: **React 19** + **Vite 6** + **Zustand 5**.  
Prod-сборка: `frontend/dist` → раздаётся FastAPI.  
Dev: Vite **5175**, proxy `/api` → `http://127.0.0.1:8005`.

См. [[Карта репозитория]] · [[Стек технологий]]

---

## Дерево src

```
src/
├── main.jsx
├── App.jsx
├── index.css
├── api/
│   ├── client.js      # fetch, cookie, CSRF, 401/429
│   ├── auth.js
│   ├── library.js
│   └── stream.js
├── components/
│   ├── MusicLibrary.jsx
│   ├── MusicLibraryPlayer.jsx
│   ├── AlbumDetail.jsx
│   ├── AuthModal.jsx
│   ├── CoverPicker.jsx
│   └── PlaylistsModal.jsx   # окно-редактор плейлистов (меню профиля)
├── store/
│   ├── authStore.js
│   └── playerStore.js
├── services/
│   ├── mediaSession.js
│   └── coverMissing.js
└── utils/
    ├── cover.js        # resolveCoverUrl — обложки не наследуются по иерархии
    ├── adminEdits.js   # trackRenamePatch — общая валидация inline-переименования
    ├── scanReport.js   # describeScan — текст отчёта кнопки «Сканировать»
    └── scanProgress.js # percentOf / scanCounter — арифметика progress bar-а скана
    api/library.js   # setCoverUrl / uploadCoverFile / resetCover шлют
                      # `tmusic:catalog-changed` с detail: {type, id, custom_key}
                      # — слушатели обновляют конкретную строку
```

---

## App.jsx — роли

- Скрытый нативный `<audio ref>` — единственный источник звука
- `checkAuth()` при монтировании + `setupMediaSession`
- Слушает `tmusic:unauthorized` → открывает AuthModal
- Синхронизация `src` при смене `currentTrack`
- `timeupdate` → store + MediaSession position + pre-cache @75%
- `onended` → `nextTrack`
- Рендер: `MusicLibrary` + `MusicLibraryPlayer` + модалки

---

## API client (`client.js`)

- Auth: HttpOnly cookie, `credentials: 'same-origin'`
- JWT **не** хранится в localStorage
- Мутации: `X-CSRF-Token` из readable cookie `csrf_token`
- 401 → `window.dispatchEvent('tmusic:unauthorized')`
- 429 → ждёт `Retry-After`, затем throw

---

## PWA

- `frontend/public/manifest.json` + `icon.svg`
- Media Session: обложка, исполнитель, play/pause/next/prev на lock-screen и в авто
- Код: `src/services/mediaSession.js`

См. [[Zustand stores]] · [[Media Session PWA]]

---

## Сборка

```powershell
cd K:\Test\tmusic\frontend
npm install
npm run dev      # 5175
npm run build    # → dist/
```

Docker multi-stage собирает `dist` внутри контейнера.

---

## Связанные заметки

- [[Zustand stores]]
- [[API Auth]] · [[API Library]] · [[API Stream]]
- [[Окно плейлистов]]
- [[Docker]]
- [[00-Главная]]

## Граничные значения API и очереди

`api/client.js` разбирает Retry-After как секунды или HTTP-date через `utils/retryAfter.js`. Missing/malformed значение: fallback 60 секунд; прошедшая дата: 0; UI wait cap: 300 секунд. NaN/Infinity не передаются в timer. После ожидания возвращается ошибка 429, автоматического replay нет.

`npm test` запускает Node-регрессии в tests/ без новых пакетов. Play-next сохраняет currentTrack/queueIndex и порядок next в shuffle. См. [[Ревью граничных значений 2026-10-01]].

[[00-Главная]]
