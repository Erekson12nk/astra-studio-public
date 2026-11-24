@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Build package

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo.
echo   Building the package for hand-off.
echo.
echo   The full package is about 600 MB: the program, ffmpeg
echo   and every library. The recipient needs only Python.
echo.
echo   The light package is about 165 MB, without libraries:
echo   they are installed from the internet by setup.bat
echo.
set "LIGHT="
set /p "LIGHT=Build the light one, without libraries? [y/N]: "

if /i "%LIGHT%"=="y" (
    "%PY%" build_bundle.py --no-wheels
) else (
    "%PY%" build_bundle.py
)

echo.
pause
endlocal
