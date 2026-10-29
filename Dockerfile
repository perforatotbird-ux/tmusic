# Stage 1: Build Frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

# Stage 2: Runtime Server
FROM python:3.13-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev postgresql-client curl ffmpeg && \
    rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# SECURITY (2026-09 audit): run as an unprivileged user instead of root.
# The app writes to /app/cache and /app/data, so ownership is handed over.
RUN groupadd --system tmusic && useradd --system --gid tmusic tmusic && \
    mkdir -p /app/cache /app/data && \
    chown -R tmusic:tmusic /app
USER tmusic

ENV FFMPEG_BIN_DIR=/usr/bin

WORKDIR /app/backend
EXPOSE 8005

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8005/health/ready -o /dev/null || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8005"]
