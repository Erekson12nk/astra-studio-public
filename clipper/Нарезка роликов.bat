@echo off
rem Launch clipper GUI. pyw = no console window; py = fallback.
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw "%~dp0gui.py"
) else (
    start "" py "%~dp0gui.py"
)