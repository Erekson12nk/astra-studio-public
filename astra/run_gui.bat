@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Astra

set "PY=python"
set "PYW=pythonw"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if exist ".venv\Scripts\pythonw.exe" set "PYW=.venv\Scripts\pythonw.exe"

where %PY% >nul 2>nul
if errorlevel 1 (
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo   [ERROR] Python is not installed, or not added to PATH.
        echo   Install Python 3.10+ from python.org, tick "Add python.exe to PATH",
        echo   then run setup.bat
        echo.
        pause
        exit /b 1
    )
)

echo Checking installation, please wait...
"%PY%" -c "import gui" 2>startup_error.txt
if errorlevel 1 (
    echo.
    echo   The program cannot start. Details:
    echo   ------------------------------------------------------------
    type startup_error.txt
    echo   ------------------------------------------------------------
    echo.
    echo   Run check.bat for a full diagnosis and instructions.
    echo.
    pause
    exit /b 1
)
del startup_error.txt >nul 2>nul

start "" "%PYW%" "%~dp0app.pyw" %*
endlocal
exit /b 0
