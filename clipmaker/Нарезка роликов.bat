@echo off
title Нарезка роликов
cd /d "%~dp0"

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Не найден ffmpeg. Поставь его и перезапусти:
    echo   winget install Gyan.FFmpeg
    echo.
    pause
    exit /b 1
)

where py >nul 2>&1
if errorlevel 1 goto nopython

rem ищем pythonw рядом с рабочим питоном - он открывает окно без чёрной консоли
set "PYW="
for /f "delims=" %%i in ('py -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYW=%%i"

if defined PYW if exist "%PYW%" (
    start "" "%PYW%" "%~dp0gui.pyw"
    exit /b 0
)

rem запасной путь: с консолью, зато точно запустится
py "%~dp0gui.pyw"
if errorlevel 1 pause
exit /b 0

:nopython
echo.
echo   Не найден Python. Поставь его с python.org и запусти этот файл снова.
echo.
pause
exit /b 1
