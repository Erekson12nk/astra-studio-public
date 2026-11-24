@echo off
setlocal
cd /d "%~dp0"
title Astra Studio - setup

echo ==========================================
echo   Astra Studio: installing everything
echo ==========================================
echo.

rem ---- 1. Python 3.12 (py launcher first, then python from PATH)
set PY=
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 set PY=py -3.12
if not defined PY (
    python -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>nul
    if not errorlevel 1 set PY=python
)
if not defined PY (
    echo [!] Python 3.12 not found.
    where winget >nul 2>nul
    if errorlevel 1 goto :nopython
    choice /c YN /m "Install Python 3.12 with winget now"
    if errorlevel 2 goto :nopython
    winget install -e --id Python.Python.3.12 --scope user
    py -3.12 -c "import sys" >nul 2>nul
    if errorlevel 1 goto :reopen
    set PY=py -3.12
)
echo [OK] Python:
%PY% --version

rem ---- 2. virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)
set VPY=.venv\Scripts\python.exe

rem ---- 3. packages
echo.
echo Installing packages. First run downloads about 3 GB, be patient...
"%VPY%" -m pip install --upgrade pip --quiet
if errorlevel 1 goto :fail
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
"%VPY%" -c "import cv2, mediapipe, faster_whisper, onnxruntime, PIL, pystray, sherpa_onnx, yt_dlp, streamlink"
if errorlevel 1 goto :fail
echo [OK] Python packages.

rem ---- 4. ffmpeg
echo.
if exist "ffmpeg\bin\ffmpeg.exe" (
    echo [OK] ffmpeg from the program folder.
    goto :ffdone
)
where ffmpeg >nul 2>nul
if not errorlevel 1 (
    echo [OK] ffmpeg found in PATH.
    goto :ffdone
)
echo [!] ffmpeg not found - video can not be cut without it.
where winget >nul 2>nul
if errorlevel 1 goto :ffhint
choice /c YN /m "Install ffmpeg with winget now"
if errorlevel 2 goto :ffhint
winget install -e --id Gyan.FFmpeg
echo ffmpeg installed. If the Studio does not see it, reopen Windows session or this window.
goto :ffdone
:ffhint
echo Install later:  winget install Gyan.FFmpeg
echo or unpack a build so that ffmpeg\bin\ffmpeg.exe lies in this folder.
:ffdone

rem ---- 5. optional: uploader (social-auto-upload)
echo.
if exist "tools\social-auto-upload\.venv" (
    echo [OK] Uploader already installed.
    goto :saudone
)
choice /c YN /m "Install the YouTube uploader (social-auto-upload, needs git, about 300 MB)"
if errorlevel 2 goto :saudone
where git >nul 2>nul
if errorlevel 1 (
    echo [!] git not found: winget install Git.Git   then run setup.bat again.
    goto :saudone
)
if not exist "tools\social-auto-upload" (
    git clone --depth 1 https://github.com/dreammis/social-auto-upload.git tools\social-auto-upload
    if errorlevel 1 goto :saufail
)
"%VPY%" -m pip install --quiet uv
if errorlevel 1 goto :saufail
pushd tools\social-auto-upload
"..\..\.venv\Scripts\python.exe" -m uv venv --python 3.12
if errorlevel 1 goto :saufailpop
"..\..\.venv\Scripts\python.exe" -m uv pip install --python .venv\Scripts\python.exe -e .
if errorlevel 1 goto :saufailpop
if not exist conf.py copy conf.example.py conf.py >nul
.venv\Scripts\patchright install chromium
if errorlevel 1 goto :saufailpop
popd
echo [OK] Uploader installed. Sign in to YouTube on the "Upload" tab of the Studio.
goto :saudone
:saufailpop
popd
:saufail
echo [!] Uploader was not installed. Everything else works; run setup.bat again later.
:saudone

rem ---- 6. hints
echo.
where ollama >nul 2>nul
if errorlevel 1 (
    echo [i] For "by meaning" moment search install Ollama: https://ollama.com
    echo     then run:  ollama pull qwen3:8b
    echo     Without it loudness search and the heuristic engine still work.
) else (
    echo [OK] Ollama found. Model for moment search:  ollama pull qwen3:8b
)

echo.
echo ==========================================
echo   Done. Next time start the program with
echo   the Studio .bat file next to this one.
echo ==========================================
echo.
choice /c YN /m "Start the Studio now"
if errorlevel 2 goto :end
start "" ".venv\Scripts\pythonw.exe" studio\app.pyw
:end
endlocal
exit /b 0

:nopython
echo Install Python 3.12 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH", then run setup.bat again.
goto :fail
:reopen
echo Python was installed. Close this window and run setup.bat again.
pause
endlocal
exit /b 1
:fail
echo.
echo Setup failed - see the messages above.
pause
endlocal
exit /b 1
