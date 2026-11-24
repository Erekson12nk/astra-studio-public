@echo off
cd /d "%~dp0"
set VENV=..\astra\.venv\Scripts\pythonw.exe
if exist "%VENV%" (
    start "" "%VENV%" studio\app.pyw
) else (
    start "" pythonw studio\app.pyw
)
