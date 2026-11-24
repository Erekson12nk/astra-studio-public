import os
import subprocess
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
LOG = os.path.join(ROOT, "start_error.log")


def report(title, text):
    try:
        with open(LOG, "w", encoding="utf-8") as handle:
            handle.write(text)
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import scrolledtext

        window = tk.Tk()
        window.title(title)
        tk.Label(window, text="Программа не смогла запуститься", padx=14, pady=10,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(window, padx=14, justify="left", wraplength=560,
                 text="Запустите check.bat в этой же папке — он проверит установку\n"
                      "и подскажет, чего не хватает. Текст ошибки ниже сохранён\n"
                      "в файл start_error.log рядом с программой.").pack(anchor="w")
        box = scrolledtext.ScrolledText(window, width=76, height=16, wrap="word",
                                        font=("Consolas", 9))
        box.pack(padx=14, pady=12)
        box.insert("1.0", text)
        tk.Button(window, text="Закрыть", width=14,
                  command=window.destroy).pack(pady=(0, 14))
        window.mainloop()
        return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text[:1500], title, 0x10)
    except Exception:
        pass


def same_file(left, right):
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(left) == os.path.normcase(right)


def main():
    if os.path.isfile(VENV) and not same_file(VENV, sys.executable):
        flags = 0x08000000 if os.name == "nt" else 0
        subprocess.Popen([VENV, os.path.abspath(__file__)] + sys.argv[1:],
                         cwd=ROOT, creationflags=flags)
        return

    sys.path.insert(0, ROOT)
    import gui

    gui.main()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        details = traceback.format_exc()
        missing = ""
        if "ModuleNotFoundError" in details or "ImportError" in details:
            missing = ("Похоже, не установлены нужные библиотеки.\n"
                       "Запустите setup.bat в этой папке и дождитесь окончания.\n\n")
        report("Ошибка запуска", missing + details)
