import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "Astra"
WORK = os.path.join(ROOT, "build", "launcher")


def main():
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller не установлен. Выполните:")
        print("    %s -m pip install pyinstaller" % sys.executable)
        return 1

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        "--distpath", ROOT,
        "--workpath", WORK,
        "--specpath", WORK,
        os.path.join(ROOT, "launcher.py"),
    ]
    icon = os.path.join(ROOT, "icon.ico")
    if os.path.isfile(icon):
        command += ["--icon", icon]

    print("Собираю лаунчер, это займёт минуту...")
    if subprocess.run(command).returncode != 0:
        print("Сборка не удалась")
        return 1

    shutil.rmtree(WORK, ignore_errors=True)
    target = os.path.join(ROOT, NAME + ".exe")
    if not os.path.isfile(target):
        print("Файл не появился: %s" % target)
        return 1

    print()
    print("Готово: %s" % target)
    print("Размер: %.1f МБ" % (os.path.getsize(target) / 1048576.0))
    print()
    print("Этот файл только запускает программу и не содержит её код,")
    print("поэтому после любых обновлений пересобирать его не нужно.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
