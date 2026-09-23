# Android APK (Оболочка на Android 14+)

#mobile #android #capacitor #apk #fgs

---

## Обзор

T-Music Android APK — нативная оболочка на базе **Capacitor 8** в **remote-режиме** поверх существующего React-клиента, спроектированная специально под требования **Android 14+ (API 34)**:

- **Бесшовная авторизация:** WebView открывает реальный origin сервера (`server.url`), благодаря чему сохраняются HttpOnly SameSite cookie, двойной CSRF-токен и все стандартные API-маршруты без переделки на Bearer-токены.
- **Zero-Config Wi-Fi обнаружение (LAN):** Приложение умеет автоматически сканировать локальную сеть (UDP-радар на порту 8211 + параллельный HTTP-опрос подсети `/health` по портам 8005/8000), находя запущенный в домашней сети сервер без необходимости вручную вбивать IP.
- **Поддержка локального HTTP:** Включен `usesCleartextTraffic="true"` для беспрепятственной работы с локальными серверами (`http://192.168.x.x:8005`) без необходимости выпускать SSL-сертификаты в домашней сети.
- **Фоновое воспроизведение (FGS):** Foreground Service типа `mediaPlayback` (`android:foregroundServiceType="mediaPlayback"`) предотвращает выгрузку плеера системой.
- **Интеграция с системой (Lock-Screen & Bluetooth):** `MediaSessionCompat` и `NotificationCompat.MediaStyle` синхронизируют обложки, названия треков, состояние воспроизведения и команды кнопок (Play, Pause, Next, Previous, Seek).
- **WakeLock:** `PARTIAL_WAKE_LOCK` исключает остановку аудиотрансляции при отключении экрана.

---

## Требования к окружению

1. **Java Development Kit:** JDK 17+ (`JAVA_HOME` настроен).
2. **Android SDK:**
   - Android SDK Platform 34 (Android 14) / 35.
   - Build-Tools 34.0.0+.
   - Переменная среды `ANDROID_HOME` (например, `%LOCALAPPDATA%\Android\Sdk`).
   - *Автоматическая установка:* `.\scripts\setup_android_sdk.ps1` (загрузка SDK 34, build-tools, adb, настройка окружения).
3. **HTTPS Сервер:**
   - Android 14 блокирует cleartext HTTP по умолчанию.
   - Экземпляр T-Music должен отдавать валидный HTTPS (Let's Encrypt / Tailscale HTTPS / root CA mkcert).

---

## Сборка APK

Все этапы автоматизированы в `scripts/build_android_apk.ps1`.

### 1. Сборка Debug APK с автопоиском в Wi-Fi (по умолчанию)
```powershell
.\scripts\build_android_apk.ps1
```
> При первом запуске в домашней сети приложение само просканирует Wi-Fi, найдёт запущенный сервер `http://192.168.x.x:8005` и подключится в 1 тап.

### 2. Сборка с фиксацией конкретного URL сервера
```powershell
.\scripts\build_android_apk.ps1 -ServerUrl "http://192.168.1.100:8005"
```
Итоговый файл: `mobile/app-debug.apk`.

Установка на подключённый телефон:
```powershell
adb install -r mobile/app-debug.apk
```

### 2. Сборка Release APK (подписанный)
1. Создайте ключ (один раз):
   ```powershell
   cd mobile/android
   keytool -genkeypair -v -keystore tmusic-release.jks -alias tmusic -keyalg RSA -keysize 2048 -validity 10000
   ```
2. Скопируйте `mobile/android/keystore.properties.example` в `mobile/android/keystore.properties` и укажите пароли.
3. Запустите сборку:
   ```powershell
   .\scripts\build_android_apk.ps1 -Release -ServerUrl "https://your-tmusic-domain.com"
   ```
Итоговый файл: `mobile/app-release.apk`.

---

## Структура модулей оболочки

```
mobile/
├── capacitor.config.ts        # Настройки Capacitor, URL сервера, allowNavigation
├── package.json               # Зависимости Capacitor 8 (@capacitor/android, cli, core)
├── www/
│   └── index.html             # Тёмный экран ожидания / fallback со сменой URL
└── android/
    ├── keystore.properties.example
    ├── app/
    │   ├── build.gradle       # compileSdk 35, minSdk 34, targetSdk 34, webkit & media
    │   ├── proguard-rules.pro # Правила сохранения webkit, media и JavascriptInterface
    │   └── src/main/
    │       ├── AndroidManifest.xml # Разрешения FGS, POST_NOTIFICATIONS, WAKE_LOCK
    │       └── java/app/tmusic/mobile/
    │           ├── MainActivity.kt        # Управление WebView, User-Agent, статусный бар
    │           ├── MediaPlaybackService.kt# FGS mediaPlayback, MediaSession, нотификация
    │           └── AndroidMediaBridge.kt  # Двусторонний мост между JS и Android
```

---

## Особенности настройки Android (MIUI / OneUI)

Для предотвращения агрессивной выгрузки служб в фоновом режиме:
- **Настройки → Приложения → T-Music → Батарея → Без ограничений (Unrestricted)**.
- Разрешите автозапуск в фоне (при наличии в прошивке).

---

## Связанные заметки

- [[Карта репозитория]]
- [[Frontend]]
- [[Кэш и стриминг]]
