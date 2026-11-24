import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import ffmpeg as _ff


def places():
    kept = [ROOT]
    if getattr(sys, "frozen", False):
        kept.insert(0, os.path.dirname(os.path.abspath(sys.executable)))
    bundle = getattr(sys, "_MEIPASS", "")
    if bundle:
        kept.insert(0, bundle)
    return kept


_ff.use_bundled_tools(*places())

from studio.window import main

if __name__ == "__main__":
    main()
