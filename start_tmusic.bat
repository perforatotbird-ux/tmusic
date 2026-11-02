@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion
title T-Music Server
echo ==============================================================================
echo   T-MUSIC Standalone Server (Port 8005)
echo   High-Security Telegram Cloud Audio Streamer
echo ==============================================================================
echo.

cd /d "%~dp0"

set "ROOT=%~dp0"
set "APP_PORT=8005"
set "VENV_PY=%ROOT%venv\Scripts\python.exe"
set "REQ=%ROOT%backend\requirements.txt"
set "IMPORTS=import fastapi, uvicorn, sqlalchemy, pyrogram, argon2, bcrypt, jwt, cryptography, aiohttp, pydantic_settings, pyzipper"

if not exist "music.env" (
    echo [WARN] music.env not found - creating from music.env.example...
    copy music.env.example music.env > nul
    echo [WARN] Fill in the real values in music.env before the first start.
    echo.
)

rem ---------------------------------------------------------------- 1/4: venv
if not exist "%VENV_PY%" goto :create_venv

"%VENV_PY%" -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [WARN] venv interpreter does not start - rebuilding the environment.
    if exist "%ROOT%venv\pyvenv.cfg" rmdir /s /q "%ROOT%venv"
    goto :create_venv
)

echo [1/4] venv found: "%VENV_PY%"
echo.
goto :deps

:create_venv
echo [1/4] Creating virtual environment: "%ROOT%venv" ...
set "PY_BOOT="
py -3.13 -c "import sys" >nul 2>&1 && set "PY_BOOT=py -3.13"
if not defined PY_BOOT (
    py -3.12 -c "import sys" >nul 2>&1 && set "PY_BOOT=py -3.12"
)
if not defined PY_BOOT (
    py -3.11 -c "import sys" >nul 2>&1 && set "PY_BOOT=py -3.11"
)
if not defined PY_BOOT (
    python -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>&1 && set "PY_BOOT=python"
)
if not defined PY_BOOT (
    echo [ERROR] Python 3.11+ not found. Install Python 3.13 ^(python.org^),
    echo         enable "Add python.exe to PATH", then run this file again.
    pause
    exit /b 1
)

!PY_BOOT! -m venv "%ROOT%venv"
if errorlevel 1 (
    echo [ERROR] Failed to create venv with "!PY_BOOT!".
    pause
    exit /b 1
)
if not exist "%VENV_PY%" (
    echo [ERROR] venv interpreter not found: "%VENV_PY%".
    pause
    exit /b 1
)
echo [OK] venv created with "!PY_BOOT!".
echo.
goto :deps

rem -------------------------------------------------------------- 2/4: deps
:deps
echo [2/4] Checking Python dependencies...
"%VENV_PY%" -c "%IMPORTS%" >nul 2>&1
if not errorlevel 1 goto :deps_ok

echo [WARN] Dependencies are missing - installing from backend\requirements.txt ...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r "%REQ%"
if errorlevel 1 (
    echo.
    echo [ERROR] pip install failed. See the output above.
    pause
    exit /b 1
)

"%VENV_PY%" -c "%IMPORTS%" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Dependencies still cannot be imported after install.
    pause
    exit /b 1
)

:deps_ok
echo [OK] Backend dependencies verified.
echo.

rem ------------------------------------------------------------- 3/4: port
echo [3/4] Checking port %APP_PORT%...
netstat -ano | findstr ":%APP_PORT% " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [ERROR] Port %APP_PORT% is already in use.
    echo         Run restart.bat to replace the running server, or stop it manually:
    echo         netstat -ano ^| findstr ":%APP_PORT% "
    pause
    exit /b 1
)
echo [OK] Port %APP_PORT% is free.
echo.

rem ------------------------------------------------------------ 4/4: launch
echo [4/4] Starting T-Music FastAPI server on http://localhost:%APP_PORT% ...
echo.
echo Application will be available at:
echo   - Local:    http://localhost:%APP_PORT%
echo   - Network:  http://0.0.0.0:%APP_PORT%
echo.

cd /d "%ROOT%backend"
"%VENV_PY%" -m uvicorn app.main:app --host 0.0.0.0 --port %APP_PORT%

echo.
echo [STOPPED] Server process has exited (code %errorlevel%).
pause
endlocal
