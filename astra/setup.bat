@echo off
setlocal
cd /d "%~dp0"
title Setup - Astra

echo ==========================================
echo   Setting up Astra
echo ==========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Install Python 3.10 or newer from python.org
    echo and enable "Add python.exe to PATH" during setup.
    goto :fail
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required.
    python --version
    goto :fail
)

if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :fail
)

echo Installing Python packages, this may take several minutes...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
where ffmpeg >nul 2>nul
if errorlevel 1 (
    if exist "ffmpeg\bin\ffmpeg.exe" (
        echo [OK] Using bundled ffmpeg from the project folder.
    ) else (
        echo [WARN] ffmpeg not found.
        echo Install it with:  winget install Gyan.FFmpeg
        echo Or unpack a build into:  %~dp0ffmpeg\bin\
        echo The program will not render video until ffmpeg is available.
    )
) else (
    echo [OK] ffmpeg found in PATH.
)

echo.
echo ==========================================
echo   Done. Start the program by opening
echo   the program:  Astra.exe
echo ==========================================
echo.
pause
endlocal
exit /b 0

:fail
echo.
echo Setup failed.
pause
endlocal
exit /b 1
