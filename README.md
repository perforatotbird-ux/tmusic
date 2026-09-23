# T-Music 🎵

> Автономный, высокопроизводительный сервер потокового воспроизведения аудио и музыкальная витрина поверх облачного хранилища Telegram.

---

## ⚡ О проекте

**T-Music** — это самостоятельный веб-сервис, выделенный из монолита CloudByte для независимой работы с музыкальной коллекцией. Приложение ориентировано на высокую скорость, низкое потребление ресурсов и бескомпромиссную безопасность при публикации в публичный Интернет.

### Ключевые возможности:
- **Zero-Copy Streaming:** Потоковая передача аудио напрямую из Telegram с поддержкой HTTP 206 Range-запросов (мгновенная перемотка треков).
- **Multi-Account Cluster & True Mid-Stream Failover:** Полноценная поддержка кластера Telegram (аккаунты Master/Slave). При возникновении `FloodWait` на одном из аккаунтов поток мгновенно и прозрачно для плеера переключается на зеркальный аккаунт без прерывания звучания.
- **In-Memory MTProto Sessions:** Сессии Telegram запускаются в оперативной памяти (`in_memory=True`), исключая конфликты файловых блокировок SQLite между сервисами.
- **RAM Cache:** Буферизация активных треков в оперативной памяти для исключения повторных сетевых запросов при переключении.
- **Background Pre-caching:** Упреждающая подгрузка начальных 2 МБ следующих 2 треков из очереди для бесшовного воспроизведения без пауз между композициями.
- **Постоянный кэш обложек (TTL=0):** Перманентное хранение графических обложек на диске без риска их потери после перезагрузки.
- **Изолированная инфраструктура:** Собственная база данных PostgreSQL (`tgmusic`), независимый кластер MTProto-сессий Telegram, собственный конфиг `music.env`.
- **PWA & Media Session API:** Нативное отображение обложки, исполнителя и кнопок управления на экране блокировки смартфона, планшета и автомобильной мультимедиа.

---

## 🏗️ Архитектура (No God Objects)

Сервис спроектирован по принципам чистой слоистой архитектуры:
```
tmusic/
├── backend/
│   ├── app/
│   │   ├── api/             # HTTP Routers (auth, library, stream)
│   │   ├── core/            # Config, Security, Database, RateLimiter
│   │   ├── models/          # SQLAlchemy Models (VFS, TGAccount, Music, Auth)
│   │   ├── repositories/    # Data Access Layer (Artist, Album, File)
│   │   ├── schemas/         # Pydantic Schemas (V2)
│   │   ├── services/        # Business Logic (Auth, Stream, Library, CoverCache, RamCache)
│   │   └── tg/              # Telegram Transport (Multi-Account Manager, FastStreamer, Failover)
│   └── tests/               # Unit & Integration Pytest Suite
├── frontend/
│   ├── src/
│   │   ├── api/             # Typed API Clients
│   │   ├── components/      # UI Components (MusicLibrary, Player, Auth, CoverPicker)
│   │   ├── store/           # Zustand State (Player, Auth, Pre-caching)
│   │   └── services/        # MediaSession API Integration
│   └── dist/                # Production UI Bundle
├── scripts/
│   ├── migrate_data.py      # Миграция папки Music, tg_accounts и кэшей из CloudByte
│   └── create_session.py    # Интерактивная генерация сессии Telegram
└── start_tmusic.bat         # Скрипт запуска под Windows
```

---

## 🚀 Быстрый запуск

### 1. Требования
- Python 3.11+ (рекомендуется Python 3.13)
- Node.js 18+ (для пересборки фронтенда)
- PostgreSQL 15+

### 2. Запуск под Windows
Просто запустите скрипт:
```cmd
start_tmusic.bat
```
Приложение откроется на **http://localhost:8005**.

**Доступ к панели (после аудита безопасности 2026-09 дефолтные учётные данные удалены):**
- Пароль администратора задаётся переменной `ADMIN_PASSWORD_HASH` (bcrypt) в `music.env` — применяется автоматически при запуске;
- либо вручную: `python scripts/init_admin.py` (интерактивно) или `python scripts/init_admin.py --password "..."`;
- вход только по логину и паролю: `POST /api/auth/login` (быстрый PIN-вход удалён).

### 3. Telegram Кластер и Сессии
При миграции (`python scripts/migrate_data.py`) все активные аккаунты кластера Telegram автоматически переносятся в таблицу `tg_accounts` базы данных `tgmusic`. **Ключи (`session_string`, `api_hash`) хранятся в базе в зашифрованном виде (Fernet, ключ — `SESSION_SECRET`).** При запуске сервер расшифровывает сессии и инициализирует всех клиентов параллельно в оперативной памяти; незашифрованные записи автоматически перешифровываются при старте (или вручную: `python scripts/secure_tg_accounts.py`).

---

## 📱 Мобильное приложение (Android 14+)

Проект включает нативную оболочку для Android 14+ (API 34) на базе Capacitor в remote-режиме, с поддержкой:
- Фонового воспроизведения через Foreground Service (`mediaPlayback`);
- Управления и обложек на экране блокировки (`MediaSessionCompat`, `NotificationCompat.MediaStyle`);
- Управления с Bluetooth-гарнитур и проводных наушников;
- Защиты от засыпания процессора (`PARTIAL_WAKE_LOCK`).

### Сборка APK:
```powershell
# Сборка Debug APK под ваш сервер:
.\scripts\build_android_apk.ps1 -ServerUrl "https://your-tmusic-domain.com"

# Установка на подключённое устройство через ADB:
adb install -r mobile/app-debug.apk
```
Подробности сборки, настройки ключей Release и оптимизации батареи: [`mobile/README.md`](mobile/README.md) и заметка в базе знаний `obsidian/10-Мобильное/Android APK.md`.

---

## 🔒 Безопасность

Подробное описание мер безопасности и защиты интернет-доступа приведено в файле [SECURITY.md](SECURITY.md).
