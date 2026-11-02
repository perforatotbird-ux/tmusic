@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion
title T-Music Server (restart)
echo ==============================================================================
echo   T-MUSIC Restart (Port 8005)
echo ==============================================================================
echo.

cd /d "%~dp0"

set "APP_PORT=8005"

rem --------------------------------------------------------------- 1/2: stop
echo [1/2] Stopping old server on port %APP_PORT% ...
set "FOUND="
set "KILLED="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%APP_PORT% " ^| findstr "LISTENING"') do (
    set "FOUND=1"
    echo !KILLED! | findstr /C:"[%%a]" >nul
    if errorlevel 1 (
        echo   Killing PID %%a
        taskkill /F /PID %%a >nul 2>&1
        set "KILLED=!KILLED![%%a]"
    )
)
if not defined FOUND echo   Nothing is listening on port %APP_PORT% - starting fresh.

rem Wait until the socket is actually released (max ~10s).
for /l %%i in (1,1,10) do (
    netstat -ano | findstr ":%APP_PORT% " | findstr "LISTENING" >nul 2>&1
    if errorlevel 1 goto :port_free
    ping -n 2 127.0.0.1 >nul
)
echo.
echo [ERROR] Port %APP_PORT% is still in use after the kill attempt.
echo         Find the owner with: netstat -ano ^| findstr ":%APP_PORT% "
pause
exit /b 1

:port_free
echo [OK] Port %APP_PORT% released.
echo.

rem ---------------------------------------------- 2/2: start (venv + deps + app)
echo [2/2] Starting server via start_tmusic.bat ...
echo.
if not exist "%~dp0start_tmusic.bat" (
    echo [ERROR] start_tmusic.bat not found next to restart.bat.
    pause
    exit /b 1
)
call "%~dp0start_tmusic.bat"

endlocal
exit /b 0
