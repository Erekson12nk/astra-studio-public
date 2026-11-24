import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

LOG = os.path.join(ROOT, "start_error.log")


def show_error(text):
    try:
        with open(LOG, "w", encoding="utf-8") as handle:
            handle.write(text)
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text[:1500], "Astra", 0x10)
    except Exception:
        sys.stderr.write(text + "\n")


if __name__ == "__main__":
    try:
        import gui

        gui.main()
    except Exception:
        show_error(traceback.format_exc())
