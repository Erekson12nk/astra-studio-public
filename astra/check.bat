@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Diagnostics

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "%~dp0diagnose.py"
    goto :done
)

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [ERROR] Python is not installed, or it was not added to PATH.
    echo.
    echo   Install Python 3.10 or newer from https://www.python.org/downloads/
    echo   During installation TICK the box "Add python.exe to PATH".
    echo   Then run setup.bat, and this check again.
    echo.
    goto :done
)

python "%~dp0diagnose.py"

:done
echo.
pause
endlocal
