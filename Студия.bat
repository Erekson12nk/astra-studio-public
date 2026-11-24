@echo off
cd /d "%~dp0"
set VENV=.venv\Scripts\pythonw.exe
if not exist "%VENV%" set VENV=..\astra\.venv\Scripts\pythonw.exe
if not exist "%VENV%" (
    echo Not installed yet. Run setup.bat first - it installs everything.
    pause
    exit /b 1
)
start "" "%VENV%" studio\app.pyw
