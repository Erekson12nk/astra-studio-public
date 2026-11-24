"""Точка входа: окно AudioClean.

    python main.py             - открыть программу
    python main.py video.mp4   - сразу подставить файл
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if os.path.isdir(os.path.join(_ROOT, "core")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from audioclean.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
