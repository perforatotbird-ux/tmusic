@echo off
chcp 65001 > nul
title T-Music Server
echo ==============================================================================
echo   T-MUSIC Restart (Port 8005)
echo ==============================================================================

cd /d "%~dp0"

echo [1/3] Stopping old server on port 8005...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8005" ^| findstr "LISTENING"') do (
    echo   Killing PID %%a
    taskkill /F /PID %%a > nul 2>&1
)
timeout /t 2 /nobreak > nul

echo [2/3] Checking Python dependencies...
cd backend
python -c "import fastapi, uvicorn, sqlalchemy, pyrogram, passlib; print('[OK] Backend dependencies verified')" 2>nul
if errorlevel 1 (
    echo [ERROR] Missing python dependencies. Please run: pip install -r requirements.txt
    pause
    exit /b 1
)

echo [3/3] Starting T-Music FastAPI Server on http://localhost:8005...
echo.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8005

pause
