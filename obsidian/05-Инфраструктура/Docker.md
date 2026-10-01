# Docker

#tmusic #infra #docker

Файлы: `Dockerfile`, `docker-compose.yml`

---

## Dockerfile (multi-stage)

### Stage 1 — frontend-builder
```
FROM node:20-alpine
WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm ci
COPY frontend ./
RUN npm run build
```

### Stage 2 — runtime
```
FROM python:3.13-slim
apt: gcc, libpq-dev, curl, postgresql-client
pip install -r backend/requirements.txt
COPY backend + frontend/dist
groupadd/useradd tmusic → USER tmusic
WORKDIR /app/backend
EXPOSE 8005
HEALTHCHECK curl 127.0.0.1:8005
CMD uvicorn app.main:app --host 0.0.0.0 --port 8005
```

Security:
- Non-root `tmusic`
- HEALTHCHECK interval 30s / timeout 5s / start-period 25s / retries 3
- Владение `/app/cache` и `/app/data` передано `tmusic`

---

## docker-compose.yml

| Параметр | Значение |
|----------|----------|
| service | `tmusic-app` |
| container_name | `tmusic-server` |
| restart | `unless-stopped` |
| ports | `8005:8005`, `8211:8211/udp` (LAN discovery) |
| volumes | `./music.env:/app/music.env:ro`, `./cache:/app/cache`, `./data:/app/data` |
| extra_hosts | `host.docker.internal:host-gateway` |
| security_opt | `no-new-privileges:true` |
| cap_drop | `ALL` |
| cap_add | `NET_BIND_SERVICE` |
| read_only | `false` (cache/data writable) |

PostgreSQL доступен в optional profile `db`. Пароль в Compose отсутствует: сервис читает `/run/secrets/postgres_password` через `POSTGRES_PASSWORD_FILE`. Файл-источник `.secrets/postgres_password` исключён из Git и Docker build context; контейнер приложения этот secret не получает. Порт опубликован только на `127.0.0.1:5432`, внутри Compose БД доступна как `db:5432`.

До первого запуска профиля:

```powershell
python scripts/init_db_secret.py
# В приватном music.env заполнить DATABASE_URL для db:5432.
# Пароль взять из локального secret-файла, не вставлять в команды/документацию.
docker compose --profile db up -d --build
```

Скрипт создаёт случайный пароль, не выводит его и отказывается перезаписывать существующий файл. См. [[PostgreSQL]] и [[Аудит секретов 2026-10-01]]. Для внешней БД профиль `db` и локальный secret-файл не нужны.

> `POSTGRES_PASSWORD_FILE` используется образом PostgreSQL при первичной инициализации пустого volume. Для существующей БД новая запись в файле **не меняет пароль роли**. Не удалять `tmusic_pgdata` ради смены пароля.

---

## Запуск

```powershell
cd K:\Test\tmusic
# music.env уже заполнен?
docker compose up -d --build
docker compose logs -f tmusic-app
```

Логи/статус:
```powershell
docker ps
docker inspect tmusic-server --format '{{json .State.Health}}'
```

---

## Сборка образа отдельно

```powershell
docker build -t tmusic:local .
docker run --rm -p 8005:8005 --env-file music.env tmusic:local
```

---

## Практические заметки

- `DATABASE_URL` внутри контейнера: `localhost` — это **контейнер**, не хост. Используй `host.docker.internal` или сеть compose.
- Секреты не запекать в image — только volume/env-file
- Кэш обложек переживает пересоздание контейнера (volume)
- HEALTHCHECK проверяет HTTP `/health`; `/` отдаёт SPA shell
- `.dockerignore` исключает локальные secrets, SQL dumps, node_modules и build-артефакты
- `data/vfs_imports` находится под writable `/app/data` и очищается по TTL

---

Связано: [[Запуск Windows]] · [[music.env]] · [[Архитектура безопасности]]

[[00-Главная]]
