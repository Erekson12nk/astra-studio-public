@echo off
title AudioClean - install
setlocal
cd /d "%~dp0"
call :findpy
if errorlevel 1 goto nopy

echo Using: %PY%
%PY% -m pip install -r "%~dp0requirements.txt"
echo.
ffmpeg -version >nul 2>&1 || echo [!] ffmpeg not found in PATH - get it from https://ffmpeg.org
pause
exit /b 0

:nopy
echo [!] Python not found.
echo     Install Python 3.9+ from https://python.org
echo     If "python" opens Microsoft Store, turn off the App execution alias:
echo     Settings - Apps - Advanced app settings - App execution aliases - python.exe = Off
pause
exit /b 1

:findpy
py -3 -c "" >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
    exit /b 0
)
python -c "" >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    exit /b 0
)
exit /b 1
