import os
import sys
import traceback

if getattr(sys, "frozen", False):
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, BASE)

LOG = os.path.join(BASE, "start_error.log")


def show_error(text):
    try:
        with open(LOG, "w", encoding="utf-8") as handle:
            handle.write(text)
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import scrolledtext

        window = tk.Tk()
        window.title("Ошибка запуска")
        tk.Label(window, text="Программа не смогла запуститься", padx=14, pady=10,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        box = scrolledtext.ScrolledText(window, width=76, height=16, wrap="word",
                                        font=("Consolas", 9))
        box.pack(padx=14, pady=12)
        box.insert("1.0", text)
        tk.Button(window, text="Закрыть", width=14, command=window.destroy).pack(pady=(0, 14))
        window.mainloop()
        return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text[:1500], "Ошибка запуска", 0x10)
    except Exception:
        pass


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--engine":
        import auto_vertical_editor

        sys.argv = [sys.argv[0]] + sys.argv[2:]
        try:
            auto_vertical_editor.main()
        except Exception as error:
            sys.stderr.write("error: %s\n" % error)
            sys.exit(1)
        return

    import gui

    gui.main()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        show_error(traceback.format_exc())
