import ctypes
import os
import subprocess
import sys

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def here():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def alert(text, title="Astra"):
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        sys.stderr.write(text + "\n")


def find_python(root):
    candidates = [
        os.path.join(root, ".venv", "Scripts", "pythonw.exe"),
        os.path.join(root, "python", "pythonw.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    for name in ("pythonw.exe", "pythonw"):
        found = shutil_which(name)
        if found:
            return found
    return None


def shutil_which(name):
    import shutil

    return shutil.which(name)


def main():
    root = here()
    entry = os.path.join(root, "app.pyw")
    if not os.path.isfile(entry):
        alert("Рядом с этим файлом нет app.pyw.\n\nПоложите ярлык или сам файл\n"
              "в папку с программой:\n%s" % root)
        return 1

    runtime = find_python(root)
    if runtime is None:
        alert("Не найден Python.\n\n"
              "Установите Python 3.10 или новее с python.org,\n"
              "обязательно отметив «Add python.exe to PATH»,\n"
              "затем запустите setup.bat в папке программы.")
        return 1

    checker = runtime.replace("pythonw.exe", "python.exe")
    if os.path.isfile(checker):
        probe = subprocess.run(
            [checker, "-c", "import gui, cv2, numpy"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=root,
            creationflags=NO_WINDOW,
        )
        if probe.returncode != 0:
            detail = probe.stderr.decode("utf-8", "ignore").strip().splitlines()
            alert("Программа не может запуститься.\n\n%s\n\n"
                  "Запустите setup.bat в папке программы,\n"
                  "а при повторной ошибке — check.bat"
                  % (detail[-1] if detail else "не установлены библиотеки"))
            return 1

    subprocess.Popen([runtime, entry] + sys.argv[1:], cwd=root, creationflags=NO_WINDOW)
    return 0


if __name__ == "__main__":
    sys.exit(main())
