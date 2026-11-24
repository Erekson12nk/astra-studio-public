from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

REMUX_SUFFIX = ".seekable.mp4"

REMUX = "перекладки контейнера"
SUBS = "субтитры"
ANSWERS = "ответы движка"
OTHER = "прочее"

ORDER = (REMUX, ANSWERS, SUBS, OTHER)


def ave_folder() -> Path:
    return Path(tempfile.gettempdir()) / "ave_cache"


def kind(path) -> str:
    name = Path(path).name.lower()
    if name.endswith(REMUX_SUFFIX):
        return REMUX
    if name.endswith(".srt"):
        return SUBS
    if name.endswith(".json"):
        return ANSWERS
    return OTHER


def remux_name(source) -> str:
    source = os.path.abspath(str(source))
    try:
        stamp = "%s|%d|%d" % (source, os.path.getsize(source),
                              int(os.path.getmtime(source)))
    except OSError:
        return ""
    return hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]


def remux_beside(source) -> Path:
    source = Path(str(source)).absolute()
    return source.parent / ("%s%s" % (remux_name(source), REMUX_SUFFIX))


def beside(sources) -> list[dict]:
    folders = []
    for source in sources:
        folder = Path(str(source)).absolute().parent
        if folder.is_dir() and folder not in folders:
            folders.append(folder)
    items = []
    for folder in folders:
        for path in sorted(folder.glob("*%s" % REMUX_SUFFIX)):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            items.append({"path": path, "size": size, "kind": REMUX})
    return items


def owner(path, sources):
    stem = source_stem(path)
    if not stem:
        return None
    for source in sources:
        text = str(source)
        if not os.path.isfile(text):
            continue
        if Path(text).stem == stem or remux_name(text) == stem:
            return text
    return None


def source_stem(path) -> str:
    name = Path(path).name
    if not name.endswith(REMUX_SUFFIX):
        return ""
    return name[:-len(REMUX_SUFFIX)]


def scan(*folders) -> list[dict]:
    seen = set()
    items = []
    for folder in folders:
        if folder is None:
            continue
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            real = os.path.abspath(str(path))
            if real in seen:
                continue
            seen.add(real)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            items.append({"path": path, "size": size, "kind": kind(path)})
    return items


def by_kind(items) -> dict:
    groups = {}
    for item in items:
        cell = groups.setdefault(item["kind"], {"count": 0, "size": 0})
        cell["count"] += 1
        cell["size"] += item["size"]
    return groups


def orphans(items, sources) -> list[dict]:
    return [item for item in items
            if item["kind"] == REMUX and owner(item["path"], sources) is None]


def remove(items) -> tuple[int, int]:
    gone = 0
    freed = 0
    for item in items:
        try:
            os.remove(str(item["path"]))
        except OSError:
            continue
        gone += 1
        freed += item["size"]
    return gone, freed


def human(size) -> str:
    step = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if step < 1024 or unit == "ТБ":
            if unit == "Б":
                return "%d %s" % (int(step), unit)
            return "%.1f %s" % (step, unit)
        step /= 1024
    return "%.1f ТБ" % step
