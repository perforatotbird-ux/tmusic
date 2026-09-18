# ADR-003 Streaming Cache

#tmusic #adr #streaming #performance

| Поле | Значение |
|------|----------|
| Статус | **Accepted** |
| Решение | Многоуровневый кэш: RAM audio + pre-cache + permanent cover disk cache + HTTP 206 |

---

## Контекст

Источник байтов — Telegram Cloud через MTProto:
- latency сети
- FloodWait / rate limits
- дорогой повторный download при перемотке и переключении
- mobile-клиенты ждут seamless gapless playback

## Решение

### 1. RAM audio cache
- LRU по трекам
- Лимиты: `RAM_CACHE_MAX_ITEMS` (15) и `RAM_CACHE_MAX_BYTES` (512MB)
- Hit → отдать Range-slice без Telegram

### 2. HTTP Range (RFC 7233)
- Native `<audio>` перематывает через Range/206
- Размер для encrypted — plaintext size из `stream_service`

### 3. On-the-fly AES-256-GCM
- `vfile.is_encrypted` → decrypt блоков в пути
- Ключи: recovery + fallback в config

### 4. Client pre-cache
- При >75% текущего трека prefetch следующих 2 × 2MB
- AbortController при смене очереди
- Bounded Set id (~500)

### 5. Cover disk cache TTL=0
- Обложки не должны теряться после reboot/RAM clear
- Путь `cache/music_covers` (Docker volume)

### 6. TG failover
- FloodWait на аккаунте → mid-stream switch
- `vfile.account_map` для message-id на зеркалах

## Следствия

### +
- Низкие паузы, UX как у «локального» плеера
- Меньше нагрузки на Telegram-аккаунты
- Кэш ограничен по RAM → нет OOM-DoS

### −
- Сложность lifecycle/abort
- Нужно корректно считать plaintext size у encrypted tracks
- Инвалидация audio-кэша при замене файла в Telegram

## Связь

- [[Кэш и стриминг]]
- [[API Stream]]
- [[Telegram кластер]]
- [[Zustand stores]]

---

[[00-Главная]]
