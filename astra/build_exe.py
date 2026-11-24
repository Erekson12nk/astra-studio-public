import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(ROOT)
NAME = "Astra"
DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, "build", "pyinstaller")

COLLECT_ALL = ["mediapipe", "faster_whisper", "ctranslate2"]
COLLECT_DATA = ["cv2", "tokenizers", "huggingface_hub"]
HIDDEN = [
    "auto_vertical_editor", "gui",
    "app_client", "sqlite3",
    "core", "core.ffmpeg", "core.timecode", "core.ui", "core.settings",
    "cv2", "numpy", "PIL.Image", "PIL.ImageTk", "PIL.ImageDraw", "PIL.ImageFont",
    "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
    "tkinter.colorchooser", "tkinter.scrolledtext",
]
SIDE_FILES = ["censor.txt", "README.md"]


def check_pyinstaller():
    try:
        import PyInstaller

        print("PyInstaller %s" % PyInstaller.__version__)
        return True
    except ImportError:
        print("PyInstaller не установлен. Выполните:")
        print("    %s -m pip install pyinstaller" % sys.executable)
        return False


def build():
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", NAME,
        "--windowed",
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", WORK,
        "--paths", ROOT,
        "--paths", REPO,
    ]
    for package in COLLECT_ALL:
        command += ["--collect-all", package]
    for package in COLLECT_DATA:
        command += ["--collect-data", package]
    for module in HIDDEN:
        command += ["--hidden-import", module]
    command += ["--add-data", "%s%s." % (os.path.join(ROOT, "censor.txt"), os.pathsep)]

    models = os.path.join(ROOT, "models")
    if os.path.isdir(models):
        command += ["--add-data", "%s%smodels" % (models, os.pathsep)]

    command.append(os.path.join(ROOT, "app_main.py"))

    print()
    print("Сборка началась. Это займёт несколько минут.")
    print()
    result = subprocess.run(command)
    return result.returncode == 0


def stage_extras():
    target = os.path.join(DIST, NAME)
    if not os.path.isdir(target):
        print("Папка сборки не найдена: %s" % target)
        return False

    for name in SIDE_FILES:
        source = os.path.join(ROOT, name)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(target, name))
            print("   + %s" % name)

    destination = os.path.join(target, "ffmpeg", "bin")
    os.makedirs(destination, exist_ok=True)
    for tool in ("ffmpeg", "ffprobe"):
        found = shutil.which(tool)
        if found:
            shutil.copy2(found, os.path.join(destination, tool + ".exe"))
            print("   + %s.exe" % tool)
        else:
            print("   ! %s не найден" % tool)

    face_cache = os.path.join(os.path.expanduser("~"), ".cache", "auto_vertical_editor")
    model_name = "blaze_face_short_range.tflite"
    for source in (os.path.join(ROOT, "models", model_name),
                   os.path.join(face_cache, model_name)):
        if os.path.isfile(source):
            models = os.path.join(target, "models")
            os.makedirs(models, exist_ok=True)
            shutil.copy2(source, os.path.join(models, model_name))
            print("   + модель детекции лица")
            break

    leaked = [name for name in ("creator", "license.key", "creator_private.key",
                               "accounts")
              if os.path.exists(os.path.join(target, name))]
    if leaked:
        print("ВНИМАНИЕ, в сборку попало лишнее: %s" % ", ".join(leaked))
        return False
    return True


def pack():
    import zipfile

    target = os.path.join(DIST, NAME)
    archive = os.path.join(ROOT, "build", NAME + "-exe.zip")
    os.makedirs(os.path.dirname(archive), exist_ok=True)
    if os.path.exists(archive):
        os.remove(archive)

    items = []
    for base, _dirs, files in os.walk(target):
        for name in files:
            items.append(os.path.join(base, name))

    print("Упаковываю %d файлов, это займёт около минуты..." % len(items))
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for item in items:
            bundle.write(item, os.path.join(NAME, os.path.relpath(item, target)))
    print("Архив: %s (%.0f МБ)" % (archive, os.path.getsize(archive) / 1048576.0))
    return archive


def report():
    target = os.path.join(DIST, NAME)
    total = 0
    for base, _dirs, files in os.walk(target):
        for name in files:
            total += os.path.getsize(os.path.join(base, name))
    print()
    print("Готово: %s" % target)
    print("Размер: %.0f МБ" % (total / 1048576.0))
    print()
    print("Запуск: %s.exe внутри этой папки" % NAME)
    print("Папку целиком можно заархивировать и передать - Python не нужен.")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not check_pyinstaller():
        return 1
    if not build():
        print()
        print("Сборка не удалась, смотрите вывод выше.")
        return 1
    print()
    print("Докладываю файлы рядом с программой:")
    if not stage_extras():
        return 1
    report()
    if "--no-zip" not in argv:
        print()
        pack()
    return 0


if __name__ == "__main__":
    sys.exit(main())
