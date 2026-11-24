import argparse
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(ROOT)
NAME = "Astra"

SHIP_PACKAGES = ["core"]

SHIP_FILES = [
    "auto_vertical_editor.py",
    "gui.py",
    "app_client.py",
    "app.pyw",
    "launcher.py",
    "start.pyw",
    "diagnose.py",
    "check.bat",
    "run_gui.bat",
    "censor.txt",
    "requirements.txt",
    "README.md",
]

NEVER_SHIP = ["creator", "license.key", "accounts", ".venv", "__pycache__",
              "start_error.log", "startup_error.txt", "build", "build_bundle.py"]

OFFLINE_SETUP = """@echo off
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
    echo and TICK "Add python.exe to PATH" during installation.
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

if exist "wheels" (
    echo Installing bundled packages, no internet needed...
    ".venv\\Scripts\\python.exe" -m pip install --upgrade pip --quiet --no-index --find-links wheels
    ".venv\\Scripts\\python.exe" -m pip install --no-index --find-links wheels -r requirements.txt
) else (
    echo Installing packages from the internet...
    ".venv\\Scripts\\python.exe" -m pip install --upgrade pip --quiet
    ".venv\\Scripts\\python.exe" -m pip install -r requirements.txt
)
if errorlevel 1 goto :fail

echo.
if exist "ffmpeg\\bin\\ffmpeg.exe" (
    echo [OK] ffmpeg is bundled with this package.
) else (
    where ffmpeg >nul 2>nul
    if errorlevel 1 (
        echo [WARN] ffmpeg not found. Install it:  winget install Gyan.FFmpeg
    ) else (
        echo [OK] ffmpeg found in PATH.
    )
)

echo.
echo ==========================================
echo   Done. Now run:  Astra.exe
echo   If something is wrong, run:  check.bat
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
"""


def copy_program(target):
    os.makedirs(target, exist_ok=True)
    for name in os.listdir(ROOT):
        if name.lower() == NAME.lower() + ".exe":
            shutil.copy2(os.path.join(ROOT, name), os.path.join(target, name))
            print("   + %s" % name)
    for name in SHIP_FILES:
        source = os.path.join(ROOT, name)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(target, name))
            print("   + %s" % name)
        else:
            print("   ! пропущен, нет файла: %s" % name)
    for package in SHIP_PACKAGES:
        source = os.path.join(REPO, package)
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(target, package),
                            ignore=shutil.ignore_patterns("__pycache__"),
                            dirs_exist_ok=True)
            print("   + %s/" % package)
        else:
            print("   ! пропущен, нет папки: %s" % package)
    with open(os.path.join(target, "setup.bat"), "w", encoding="ascii") as handle:
        handle.write(OFFLINE_SETUP)
    print("   + setup.bat (офлайн-версия)")


def copy_ffmpeg(target):
    destination = os.path.join(target, "ffmpeg", "bin")
    os.makedirs(destination, exist_ok=True)
    total = 0
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        if not found:
            print("   ! %s не найден, пропускаю" % tool)
            continue
        shutil.copy2(found, os.path.join(destination, tool + ".exe"))
        size = os.path.getsize(found) / 1048576.0
        total += size
        print("   + %s.exe (%.0f МБ)" % (tool, size))
    return total


def copy_face_model(target):
    from auto_vertical_editor import FACE_MODEL_NAME

    candidates = [
        os.path.join(ROOT, "models", FACE_MODEL_NAME),
        os.path.join(os.path.expanduser("~"), ".cache", "auto_vertical_editor", FACE_MODEL_NAME),
    ]
    for source in candidates:
        if os.path.isfile(source):
            destination = os.path.join(target, "models")
            os.makedirs(destination, exist_ok=True)
            shutil.copy2(source, os.path.join(destination, FACE_MODEL_NAME))
            print("   + модель детекции лица")
            return True
    print("   ! модели лица нет в кеше, получатель скачает её сам")
    return False


def download_wheels(target):
    destination = os.path.join(target, "wheels")
    os.makedirs(destination, exist_ok=True)
    print("   загружаю пакеты, это надолго и займёт сотни мегабайт...")
    result = subprocess.run([
        sys.executable, "-m", "pip", "download",
        "-r", os.path.join(ROOT, "requirements.txt"),
        "-d", destination,
    ])
    if result.returncode != 0:
        print("   ! не удалось скачать пакеты")
        return 0.0
    total = sum(os.path.getsize(os.path.join(destination, name))
                for name in os.listdir(destination)) / 1048576.0
    print("   + пакетов: %d, объём %.0f МБ" % (len(os.listdir(destination)), total))
    return total


def make_zip(folder):
    archive = folder + ".zip"
    if os.path.exists(archive):
        os.remove(archive)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for base, _dirs, files in os.walk(folder):
            for name in files:
                full = os.path.join(base, name)
                bundle.write(full, os.path.join(NAME, os.path.relpath(full, folder)))
    return archive


def main():
    parser = argparse.ArgumentParser(description="Сборка комплекта для передачи")
    parser.add_argument("--no-wheels", action="store_true", help="без библиотек, их поставят из сети")
    parser.add_argument("--no-ffmpeg", action="store_true", help="без ffmpeg")
    parser.add_argument("--no-zip", action="store_true", help="оставить папкой, не архивировать")
    parser.add_argument("--out", default=os.path.join(ROOT, "build"))
    args = parser.parse_args()

    target = os.path.join(args.out, NAME)
    if os.path.isdir(target):
        shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)

    print()
    print("Файлы программы:")
    copy_program(target)

    print()
    print("Модель детекции лица:")
    copy_face_model(target)

    if not args.no_ffmpeg:
        print()
        print("Утилиты ffmpeg:")
        copy_ffmpeg(target)

    if not args.no_wheels:
        print()
        print("Библиотеки Python:")
        download_wheels(target)

    leaked = [name for name in NEVER_SHIP if os.path.exists(os.path.join(target, name))]
    if leaked:
        print()
        print("ВНИМАНИЕ, в комплект попало лишнее: %s" % ", ".join(leaked))
        return 1

    print()
    if args.no_zip:
        print("Готово: %s" % target)
        return 0
    print("Упаковываю в архив...")
    archive = make_zip(target)
    size = os.path.getsize(archive) / 1048576.0
    print()
    print("Готово: %s" % archive)
    print("Размер: %.0f МБ" % size)
    print()
    print("Что делает получатель:")
    print("  1. распаковывает архив")
    print("  2. ставит Python 3.10+ с галочкой Add python.exe to PATH")
    print("  3. запускает setup.bat")
    print("  4. открывает Astra.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
