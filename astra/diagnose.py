import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PACKAGES = [
    ("cv2", "opencv-python"),
    ("numpy", "numpy"),
    ("PIL", "pillow"),
    ("mediapipe", "mediapipe"),
    ("faster_whisper", "faster-whisper"),
]

problems = []


def line(mark, text):
    print("  %-4s %s" % (mark, text))


print()
print("=" * 62)
print("  ПРОВЕРКА УСТАНОВКИ")
print("=" * 62)
print()

print("Python")
line("OK", sys.version.split()[0])
line("", sys.executable)
if sys.version_info < (3, 10):
    problems.append("Нужен Python 3.10 или новее. Установите с python.org "
                    "и отметьте галочку 'Add python.exe to PATH'.")
    line("!!", "СЛИШКОМ СТАРЫЙ")

has_venv = os.path.isdir(os.path.join(ROOT, ".venv"))
print()
print("Окружение .venv")
if has_venv:
    line("OK", "папка .venv найдена")
else:
    line("..", "папки .venv нет (не страшно, если библиотеки стоят в системе)")

print()
print("tkinter (окна программы)")
try:
    import tkinter

    line("OK", "версия %s" % tkinter.TkVersion)
except Exception as error:
    line("!!", "НЕ РАБОТАЕТ: %s" % error)
    problems.append("Не установлен tkinter. Переустановите Python с официального сайта, "
                    "не из Microsoft Store.")

print()
print("Библиотеки")
missing = []
for module, package in PACKAGES:
    try:
        loaded = __import__(module)
        version = getattr(loaded, "__version__", "")
        line("OK", "%-18s %s" % (package, version))
    except Exception as error:
        line("!!", "%-18s НЕТ (%s)" % (package, type(error).__name__))
        missing.append(package)

if missing:
    if has_venv:
        problems.append("Не установлены пакеты: %s. Запустите setup.bat заново — "
                        "возможно, установка прервалась." % ", ".join(missing))
    else:
        problems.append("Не установлены пакеты: %s. Запустите setup.bat "
                        "и дождитесь окончания." % ", ".join(missing))

print()
print("ffmpeg")
for tool in ("ffmpeg", "ffprobe"):
    found = shutil.which(tool)
    local = os.path.join(ROOT, "ffmpeg", "bin", tool + ".exe")
    if found:
        line("OK", "%s: %s" % (tool, found))
    elif os.path.isfile(local):
        line("OK", "%s: в папке программы" % tool)
    else:
        line("!!", "%s НЕ НАЙДЕН" % tool)
        problems.append("Не установлен ffmpeg. Выполните в консоли: "
                        "winget install Gyan.FFmpeg, затем откройте новое окно.")

print()
print("Файлы программы")
for name in ("gui.py", "auto_vertical_editor.py", "censor.txt"):
    if os.path.isfile(os.path.join(ROOT, name)):
        line("OK", name)
    else:
        line("!!", "%s ОТСУТСТВУЕТ" % name)
        problems.append("В папке нет файла %s. Скопируйте папку программы заново." % name)

print()
print("Запуск интерфейса")
try:
    sys.path.insert(0, ROOT)
    import gui

    line("OK", "модуль интерфейса загружается")
except Exception as error:
    line("!!", "ОШИБКА: %s: %s" % (type(error).__name__, error))
    problems.append("Интерфейс не загружается, подробности выше.")

print()
print("=" * 62)
if problems:
    print("  НАЙДЕНЫ ПРОБЛЕМЫ (%d)" % len(problems))
    print("=" * 62)
    seen = set()
    number = 0
    for item in problems:
        if item in seen:
            continue
        seen.add(item)
        number += 1
        print()
        print("%d. %s" % (number, item))
else:
    print("  ВСЁ В ПОРЯДКЕ. Программа должна запускаться.")
    print("=" * 62)
print()
