# Запуск Windows

#tmusic #infra #windows

Скрипт: `start_tmusic.bat` (и `restart.bat`)

---

## Требования

| Компонент | Версия |
|-----------|--------|
| Python | 3.11+ (рек. 3.13) |
| PostgreSQL | 15+ с БД `tgmusic` |
| Node.js | 18+ (только для пересборки frontend) |

Установка зависимостей backend:

```powershell
cd K:\Test\tmusic\backend
pip install -r requirements.txt
```

---

## Что делает `start_tmusic.bat`

1. `chcp 65001`, title окна
2. `cd` в корень проекта
3. Если нет `music.env` — копирует из `music.env.example` (**нужно будет заполнить**)
4. Проверка импортов: `fastapi, uvicorn, sqlalchemy, pyrogram, passlib`
5. Запуск:
   ```
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8005
   ```

**URL:** http://localhost:8005

---

## Ручной запуск (рекомендуется для dev)

```powershell
cd K:\Test\tmusic\backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8005 --reload
```

Frontend в отдельном терминале:

```powershell
cd K:\Test\tmusic\frontend
npm install
npm run dev
# http://localhost:5175 → proxy /api → 8005
```

---

## Первичная настройка

1. Скопировать и заполнить `music.env` (см. [[music.env]])
2. Создать PostgreSQL БД `tgmusic`
3. Выставить `ADMIN_PASSWORD_HASH` или выполнить `python scripts/init_admin.py`
4. При необходимости: `python scripts/create_session.py` + миграция TG-аккаунтов
5. Запустить bat / uvicorn
6. Проверить: login через UI; `python scripts/secure_tg_accounts.py --check`

---

## Диагностика

| Симптом | Проверка |
|---------|----------|
| RuntimeError DATABASE_URL | Заполнен ли `music.env` |
| Missing python dependencies | `pip install -r requirements.txt` |
| Страница есть, аудио нет | TG-сессии, БД `tg_accounts`, логи manager |
| 401 везде | bootstrap admin, пароль |
| 429 | rate-limit; см. env |
| REQUIRE_AUTH crash | production требует auth=true |

---

Связано: [[music.env]] · [[Docker]] · [[Операционные скрипты]]

[[00-Главная]]
