from __future__ import annotations

import os
import time
from pathlib import Path

FILE_NAME = "learn.log"
LIMIT = 4000


def path(folder) -> Path:
    return Path(folder) / FILE_NAME


def write(folder, text: str) -> None:
    line = "%s  %s\n" % (time.strftime("%d.%m.%Y %H:%M"), str(text).strip())
    try:
        place = path(folder)
        place.parent.mkdir(parents=True, exist_ok=True)
        with open(place, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
    except OSError:
        pass


def read(folder, lines: int = LIMIT) -> str:
    try:
        with open(path(folder), "r", encoding="utf-8", errors="replace") as handle:
            kept = handle.read().splitlines()
    except OSError:
        return ""
    return os.linesep.join(kept[-lines:])


def clear(folder) -> None:
    try:
        os.remove(path(folder))
    except OSError:
        pass
