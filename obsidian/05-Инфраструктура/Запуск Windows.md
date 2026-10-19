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

---

## Виртуальное окружение (`venv`)

С 2026-10-18 backend живёт в **`K:\Test\tmusic\venv`** (в `.gitignore`).
`start_tmusic.bat` создаёт его сам, если папки нет, и доставляет зависимости.
Ручная установка с нуля:

```powershell
cd K:\Test\tmusic
python -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r backend\requirements.txt
# тесты (опционально): pytest + httpx
venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
```

Проверка, что окружение живо:

```powershell
venv\Scripts\python.exe -c "import fastapi, uvicorn, sqlalchemy, pyrogram, argon2, bcrypt, jwt, cryptography, aiohttp, pydantic_settings, pyzipper; print('OK')"
```

**Важно:** `requirements.txt` — только рантайм. `pytest`/`httpx` вынесены в
`backend/requirements-dev.txt`, чтобы прод-образ не тянул тестовые пакеты.
Тесты запускаются **против живой БД** — см. `AGENTS.md`.

---

## Что делает `start_tmusic.bat`

1. `chcp 65001`, title окна, `cd` в корень проекта
2. Если нет `music.env` — копирует из `music.env.example` (**нужно будет заполнить**)
3. **venv:** нет `venv\Scripts\python.exe` → создаёт окружение через `py -3`
   (fallback — `python`); нет Python в `PATH` → понятная ошибка и выход
4. **Зависимости:** проверяет импорты
   `fastapi, uvicorn, sqlalchemy, pyrogram, argon2, bcrypt, jwt, cryptography, aiohttp, pydantic_settings, pyzipper`
   в **venv-интерпретаторе**; если чего-то нет — сам делает
   `pip install -r backend\requirements.txt` и перепроверяет импорты
5. **Порт:** если 8005 уже занят — предупреждает и предлагает `restart.bat`
6. Запуск:
   ```
   venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8005
   ```

Все шаги нумерованы `[1/4]…[4/4]`, после падения uvicorn окно не закрывается
(`pause`), код выхода печатается.

**URL:** http://localhost:8005

---

## Что делает `restart.bat`

1. `[1/2]` Находит PID, слушающий **именно** порт 8005 (`netstat` + `LISTENING`,
   дубли IPv4/IPv6 схлопываются), убивает его и ждёт освобождения сокета до ~10 с
2. `[2/2]` Передаёт управление `start_tmusic.bat` — venv, зависимости и запуск
   живут в одном месте, поэтому оба скрипта не расходятся
3. Если порт не освободился — печатает подсказку `netstat -ano | findstr ":8005 "`
   и выходит с кодом 1

**Важно:** `restart.bat` не пересоздаёт venv и не переустанавливает зависимости —
это делает `start_tmusic.bat`.

---

## Ручной запуск (рекомендуется для dev)

```powershell
cd K:\Test\tmusic\backend
K:\Test\tmusic\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8005 --reload
```

(в активированном venv достаточно `python -m uvicorn ...`; `config.py` находит
`music.env` от `__file__`, поэтому `cwd` на загрузку настроек не влияет —
но запускать всё равно из `backend`.)

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
3. Выставить `ADMIN_PASSWORD_HASH` или выполнить `venv\Scripts\python.exe scripts\init_admin.py`
4. При необходимости: `venv\Scripts\python.exe scripts\create_session.py` + миграция TG-аккаунтов
5. Запустить `start_tmusic.bat` (создаст venv при необходимости) / uvicorn
6. Проверить: login через UI; `venv\Scripts\python.exe scripts\secure_tg_accounts.py --check`

Скрипты из `scripts/` тоже запускать venv-интерпретатором — иначе они подхватят
системный Python без `pyrofork`/`psycopg2`.

---

## Диагностика

| Симптом | Проверка |
|---------|----------|
| RuntimeError DATABASE_URL | Заполнен ли `music.env` |
| Missing python dependencies | `venv\Scripts\python.exe -m pip install -r backend\requirements.txt` |
| `Python not found in PATH` в bat | Python 3.11+ в `PATH` (или `py -3`) |
| Порт 8005 занят | `restart.bat`, либо `netstat -ano \| findstr ":8005 "` |
| Страница есть, аудио нет | TG-сессии, БД `tg_accounts`, логи manager |
| 401 везде | bootstrap admin, пароль |
| 429 | rate-limit; см. env |
| REQUIRE_AUTH crash | production требует auth=true |

---

Связано: [[music.env]] · [[Docker]] · [[Операционные скрипты]]

[[00-Главная]]
