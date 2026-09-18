@echo off
chcp 65001 > nul
title T-Music Backup
echo ==============================================================================
echo   T-Music — local ZIP backup
echo ==============================================================================
echo.

cd /d "%~dp0"

rem Default: code + git + music.env; skip heavy data/cache/dist
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0backup_project.ps1" %*

echo.
echo Done. Press any key to exit...
pause > nul
