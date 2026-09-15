@echo off
chcp 65001 > nul
title T-Music Server
echo ==============================================================================
echo   T-MUSIC Standalone Server (Port 8005)
echo   High-Security Telegram Cloud Audio Streamer
echo ==============================================================================
echo.

cd /d "%~dp0"

if not exist "music.env" (
    echo [WARNING] music.env not found! Creating from music.env.example...
    copy music.env.example music.env > nul
)

echo [1/2] Checking Python dependencies...
cd backend
python -c "import fastapi, uvicorn, sqlalchemy, pyrogram, passlib; print('[OK] Backend dependencies verified')" 2>nul
if errorlevel 1 (
    echo [ERROR] Missing python dependencies. Please run: pip install -r requirements.txt
    pause
    exit /b 1
)

echo [2/2] Starting T-Music FastAPI Server on http://localhost:8005...
echo.
echo Application will be available at:
echo   - Local:    http://localhost:8005
echo   - Network:  http://0.0.0.0:8005
echo.

python -m uvicorn app.main:app --host 0.0.0.0 --port 8005

pause
