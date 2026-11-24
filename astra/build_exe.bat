@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Build EXE

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo.
echo   Building the standalone EXE.
echo.
echo   The recipient needs nothing at all - no Python, no ffmpeg.
echo   About 1.5 GB, takes several minutes.
echo.
echo   Result: dist\Astra\
echo.
pause

"%PY%" build_exe.py

echo.
pause
endlocal
