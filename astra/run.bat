@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Astra 9:16

set "SCRIPT=auto_vertical_editor.py"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

if not exist "%SCRIPT%" (
    echo [ERROR] %SCRIPT% not found next to this launcher.
    goto :fail
)

if not exist ".venv\Scripts\python.exe" (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] python not found. Run setup.bat first.
        goto :fail
    )
)

if not exist "ffmpeginfmpeg.exe" (
    where ffmpeg >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] ffmpeg not found in PATH and not bundled in this folder.
        echo Install it:  winget install Gyan.FFmpeg
        echo Then open a NEW console window so PATH is refreshed.
        goto :fail
    )
)

"%PY%" -c "import cv2, mediapipe, faster_whisper" >nul 2>nul
if errorlevel 1 (
    echo [WARN] Some Python packages are missing.
    set "DOINSTALL="
    set /p "DOINSTALL=Install them now? [y/N]: "
    if /i "!DOINSTALL!"=="y" (
        "%PY%" -m pip install -r requirements.txt
        if errorlevel 1 goto :fail
    ) else (
        goto :fail
    )
)

if not "%~4"=="" (
    echo.
    echo Running: %PY% %SCRIPT% %*
    echo.
    "%PY%" "%SCRIPT%" %*
    goto :done
)

echo.
echo ==========================================
echo   Auto vertical editor  --  9:16 shorts
echo ==========================================
echo.

set "SRC=%~1"
if "!SRC!"=="" (
    set /p "SRC=Source video (drag and drop the file here): "
)
set "SRC=!SRC:"=!"
if "!SRC!"=="" (
    echo [ERROR] No source video given.
    goto :fail
)
if not exist "!SRC!" (
    echo [ERROR] File not found: !SRC!
    goto :fail
)

set "START="
set /p "START=Highlight start (HH:MM:SS): "
if "!START!"=="" (
    echo [ERROR] No start time given.
    goto :fail
)

set "END="
set /p "END=Highlight end   (HH:MM:SS): "
if "!END!"=="" (
    echo [ERROR] No end time given.
    goto :fail
)

for %%F in ("!SRC!") do set "BASE=%%~nF"
set "STAMP=!START::=-!"
set "OUT=out\!BASE!_!STAMP!.mp4"
set /p "OUT=Output file [!OUT!]: "
set "OUT=!OUT:"=!"

set "LANG=ru"
set /p "LANG=Speech language [!LANG!]: "

set "MODEL=medium"
set /p "MODEL=Whisper model tiny/base/small/medium/large-v3 [!MODEL!]: "

set "BLUR=--blur-bg"
set "USEBLUR="
set /p "USEBLUR=Blurred backdrop behind the webcam? [Y/n]: "
if /i "!USEBLUR!"=="n" set "BLUR="

set "EXTRA="
set /p "EXTRA=Extra flags (press Enter to skip): "

echo.
echo ------------------------------------------
echo %PY% %SCRIPT% "!SRC!" !START! !END! "!OUT!" --language !LANG! --model !MODEL! !BLUR! !EXTRA!
echo ------------------------------------------
echo.

"%PY%" "%SCRIPT%" "!SRC!" !START! !END! "!OUT!" --language !LANG! --model !MODEL! !BLUR! !EXTRA!

:done
if errorlevel 1 (
    echo.
    echo [ERROR] Processing failed.
    goto :fail
)
echo.
echo Done.
if not "!OUT!"=="" (
    if exist "!OUT!" (
        set "OPENDIR="
        set /p "OPENDIR=Open the output folder? [Y/n]: "
        if /i not "!OPENDIR!"=="n" (
            for %%F in ("!OUT!") do explorer /select,"%%~fF"
        )
    )
)
echo.
pause
endlocal
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1
