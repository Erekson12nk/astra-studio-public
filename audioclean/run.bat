@echo off
title AudioClean
cd /d "%~dp0"
setlocal
call :findpy
if errorlevel 1 goto nopy

%PY% main.py %*
if errorlevel 1 pause
exit /b 0

:nopy
echo [!] Python not found. Run install.bat first.
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
