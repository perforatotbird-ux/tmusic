# T-Music — Android 14+ Оболочка (Capacitor Remote Wrapper)

Официальная оболочка T-Music для Android 14+ (API 34), обеспечивающая фоновое воспроизведение и полноценное управление на экране блокировки / Bluetooth-гарнитурах.

---

## 1. Архитектура и принципы

- **Режим:** Remote WebView (`server.url`). WebView напрямую подключается к вашему защищённому серверу T-Music (`https://...`). Это сохраняет SameSite-cookie, CSRF-защиту и всю существующую клиентскую логику без необходимости дублирования кода.
- **Android 14 (API 34):**
  - **Foreground Service:** `MediaPlaybackService` зарегистрирован с типом `mediaPlayback` (`android:foregroundServiceType="mediaPlayback"`).
  - **Системные разрешения:** `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_MEDIA_PLAYBACK`, `POST_NOTIFICATIONS` (запрашивается при первом запуске), `WAKE_LOCK`.
  - **Медиа-сессия:** `MediaSessionCompat` + `NotificationCompat.MediaStyle` транслируют метаданные, обложки треков, таймкоды и события (Play / Pause / Next / Prev / Seek) между веб-плеером и системой Android.
  - **Фоновое аудио:** `BridgeActivity.onPause()` сохраняет активность JS-таймеров (`resumeTimers()`), а `PARTIAL_WAKE_LOCK` защищает процессор от засыпания при выключенном экране.
  - **Офлайн / Fallback:** При недоступности сервера WebView отображает `mobile/www/index.html` с возможностью повтора подключения или смены адреса сервера.

---

## 2. Предварительные требования

1. **Java Development Kit:** JDK 17+ (проверка: `java -version`).
2. **Android SDK:**
   - Platform SDK: API 34 (Android 14) / API 35
   - Build-Tools: 34.0.0+
   - Переменная среды: `ANDROID_HOME` указывает на путь к SDK (например, `%LOCALAPPDATA%\Android\Sdk`).
   - *Автоустановка:* Запустите `.\scripts\setup_android_sdk.ps1` — скрипт сам загрузит commandlinetools, установит платформу API 34, build-tools, adb и настроит переменные среды.
3. **HTTPS Сервер:**
   - Android 14 блокирует незащищённый HTTP-трафик (`cleartext: false`).
   - Сервер T-Music должен работать по `https://...` (домен с Let's Encrypt, Tailscale HTTPS или локальный CA mkcert, установленный в хранилище доверенных сертификатов Android).

---

## 3. Быстрая сборка (Debug APK)

Для сборки APK используется единый скрипт в корне репозитория:

```powershell
# Сборка с адресом вашего сервера:
.\scripts\build_android_apk.ps1 -ServerUrl "https://music.example.com"
```

Если `-ServerUrl` не указан, адрес берётся из переменной окружения `TMUSIC_SERVER_URL` или конфигурации `mobile/capacitor.config.ts`.

Готовый файл: `mobile/app-debug.apk`.

### Установка на телефон через ADB:
```powershell
adb install -r mobile/app-debug.apk
```

---

## 4. Сборка подписанного Release APK

### Шаг 1. Генерация ключа (один раз)
В каталоге `mobile/android`:
```powershell
keytool -genkeypair -v -keystore tmusic-release.jks -alias tmusic -keyalg RSA -keysize 2048 -validity 10000
```

### Шаг 2. Создание keystore.properties
Скопируйте `mobile/android/keystore.properties.example` в `mobile/android/keystore.properties` и укажите пароли:
```properties
storeFile=../tmusic-release.jks
storePassword=ваш_пароль_хранилища
keyAlias=tmusic
keyPassword=ваш_пароль_ключа
```
*(Файл `keystore.properties` и `*.jks` уже добавлены в `.gitignore`)*

### Шаг 3. Запуск сборки Release:
```powershell
.\scripts\build_android_apk.ps1 -Release -ServerUrl "https://music.example.com"
```
Готовый артефакт появится в `mobile/app-release.apk`.

---

## 5. Настройки энергосбережения (MIUI, OneUI, Pixel)

Для непрерывного воспроизведения более 30 минут без сброса системой:
1. Зайдите в **Настройки → Приложения → T-Music → Батарея**.
2. Установите режим **«Без ограничений»** (Unrestricted / No restrictions).
3. При необходимости включите автозапуск приложения в фоне.

---

## 6. Структура исходного кода

- `mobile/capacitor.config.ts` — конфигурация Capacitor, целевой сервер и appId (`app.tmusic.mobile`).
- `mobile/www/index.html` — тёмный экран ожидания / ошибки соединения со сменой URL сервера.
- `mobile/android/app/src/main/AndroidManifest.xml` — разрешения FGS, регистрация сервиса и медиа-кнопок.
- `mobile/android/app/src/main/java/app/tmusic/mobile/`:
  - `MainActivity.kt` — запуск WebView, User-Agent, статусный бар `#141210`, обход блокировки фонового воспроизведения.
  - `MediaPlaybackService.kt` — Foreground Service `mediaPlayback`, `MediaSessionCompat`, lock-screen notification.
  - `AndroidMediaBridge.kt` — JavaScriptInterface `AndroidMediaBridge` для двусторонней связи JS ↔ Native.
