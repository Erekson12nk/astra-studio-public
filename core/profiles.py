from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from core import asr as _asr

FOLDER_NAME = "astra-studio"
PROFILES_NAME = "profiles"
FILE_NAME = "profile.json"
ACTIVE_NAME = "active.txt"
CLIPS_NAME = "clips"
DB_NAME = "clips.db"
RERANK_NAME = "rerank.json"
FACE_STEM = "face"
DEFAULT_KEEP_DAYS = 3
DEFAULT_WHISPER = _asr.DEFAULT_MODEL
PHOTO_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
CLIP_EXT = {".mp4", ".mkv", ".mov", ".webm"}
KEEP_PREFIX = "_"


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(os.path.abspath(sys.executable)))
    return Path(__file__).resolve().parent.parent


def root() -> Path:
    base = os.environ.get("ASTRA_PROFILES")
    if base:
        return Path(base)
    return app_root() / PROFILES_NAME


def former_root() -> Path:
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(local) / FOLDER_NAME / PROFILES_NAME


def former_db() -> Path:
    work = os.environ.get("CLIPPER_WORK") or r"C:\clipper-work"
    return Path(work) / "clipper.sqlite3"


def clips_dir(profile: dict) -> Path:
    given = str(profile.get("out_dir") or "").strip()
    if given:
        return Path(given)
    return Path(profile["folder"]) / CLIPS_NAME


def db_path(profile: dict) -> Path:
    return Path(profile["folder"]) / DB_NAME


def rerank_path(profile: dict) -> Path:
    return Path(profile["folder"]) / RERANK_NAME


def former_rerank() -> Path:
    work = os.environ.get("CLIPPER_WORK") or r"C:\clipper-work"
    return Path(work) / RERANK_NAME


def adopt_former_rerank(report=None) -> bool:
    old = former_rerank()
    if not old.is_file():
        return False
    here = names()
    chosen = active_name() if active_name() in here else (
        here[0] if len(here) == 1 else "")
    if not chosen:
        if report:
            report("общая модель вкуса осталась в %s: профилей несколько, "
                   "непонятно, чья она. Обучите заново — py cli.py train" % old.parent)
        return False
    target = folder(chosen) / RERANK_NAME
    if target.exists():
        return False
    try:
        shutil.move(str(old), str(target))
    except OSError:
        return False
    if report:
        report("прежняя модель вкуса перенесена в профиль %s" % chosen)
    return True


def migrate(report=None) -> list[str]:
    if os.environ.get("ASTRA_PROFILES") or names():
        return []
    place = root()
    was = former_root()
    if not was.is_dir():
        return []
    try:
        if was.resolve() == place.resolve():
            return []
    except OSError:
        return []
    moved = []
    place.mkdir(parents=True, exist_ok=True)
    for item in sorted(was.iterdir()):
        if not (item / FILE_NAME).is_file():
            continue
        try:
            shutil.move(str(item), str(place / item.name))
        except OSError:
            continue
        moved.append(item.name)
        if report:
            report("профиль переехал в папку программы: %s" % item.name)
    marker = was / ACTIVE_NAME
    if moved and marker.is_file():
        try:
            shutil.move(str(marker), str(place / ACTIVE_NAME))
        except OSError:
            pass
    if moved:
        adopt_former_db(moved, report)
    return moved


def adopt_former_db(moved: list[str], report=None) -> bool:
    old = former_db()
    if not old.is_file():
        return False
    chosen = active_name() if active_name() in moved else (
        moved[0] if len(moved) == 1 else "")
    if not chosen:
        return False
    target = folder(chosen) / DB_NAME
    if target.exists():
        return False
    try:
        shutil.copy2(str(old), str(target))
    except OSError:
        return False
    if report:
        report("прежние оценки и сравнения легли в профиль %s" % chosen)
    return True


def folder(name: str) -> Path:
    return root() / safe_name(name)


def safe_name(name: str) -> str:
    cleaned = "".join(c for c in str(name).strip() if c not in '\\/:*?"<>|').strip()
    if not cleaned:
        raise ValueError("имя профиля пустое")
    return cleaned


def names() -> list[str]:
    base = root()
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if (p / FILE_NAME).is_file())


def load(name: str) -> dict:
    path = folder(name) / FILE_NAME
    if not path.is_file():
        raise FileNotFoundError("нет профиля %s" % name)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["name"] = data.get("name") or name
    data["folder"] = str(folder(name))
    return data


def save(profile: dict) -> dict:
    place = folder(profile["name"])
    place.mkdir(parents=True, exist_ok=True)
    stored = {k: v for k, v in profile.items() if k != "folder"}
    (place / FILE_NAME).write_text(
        json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    profile["folder"] = str(place)
    return profile


def create(name: str, photo, keep_days: int = DEFAULT_KEEP_DAYS,
           whisper: str = DEFAULT_WHISPER, language: str = "ru",
           out_dir=None, layout: str = "auto") -> dict:
    name = safe_name(name)
    if folder(name).is_file() or (folder(name) / FILE_NAME).is_file():
        raise FileExistsError("профиль %s уже есть" % name)
    if not photo:
        raise ValueError("для профиля нужна фотография стримера")
    source = Path(str(photo)).expanduser()
    if not source.is_file():
        raise FileNotFoundError("не вижу фотографию: %s" % source)
    if source.suffix.lower() not in PHOTO_EXT:
        raise ValueError("фотография должна быть картинкой, а не %s" % source.suffix)
    place = folder(name)
    place.mkdir(parents=True, exist_ok=True)
    target = place / (FACE_STEM + source.suffix.lower())
    shutil.copy2(str(source), str(target))
    if not out_dir:
        (place / CLIPS_NAME).mkdir(exist_ok=True)
    return save({
        "name": name,
        "created": time.time(),
        "face": target.name,
        "keep_days": int(keep_days),
        "whisper": whisper,
        "language": language,
        "out_dir": str(out_dir) if out_dir else "",
        "layout": layout,
    })


def remove(name: str) -> None:
    shutil.rmtree(str(folder(name)), ignore_errors=True)
    if active_name() == safe_name(name):
        (root() / ACTIVE_NAME).unlink(missing_ok=True)


def face_path(profile: dict):
    face = profile.get("face")
    if not face:
        return None
    path = Path(profile["folder"]) / face
    return path if path.is_file() else None


def active_name() -> str:
    marker = root() / ACTIVE_NAME
    if not marker.is_file():
        return ""
    return marker.read_text(encoding="utf-8").strip()


def set_active(name: str) -> str:
    name = safe_name(name)
    if not (folder(name) / FILE_NAME).is_file():
        raise FileNotFoundError("нет профиля %s" % name)
    root().mkdir(parents=True, exist_ok=True)
    (root() / ACTIVE_NAME).write_text(name, encoding="utf-8")
    return name


def active():
    name = active_name()
    if not name:
        return None
    try:
        return load(name)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return None


def protected(db_path) -> set:
    path = Path(str(db_path))
    if not path.is_file():
        return set()
    import sqlite3

    try:
        db = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    except sqlite3.Error:
        return set()
    try:
        marked = set()
        for row in db.execute("SELECT DISTINCT candidate_id FROM ratings"):
            marked.add(row[0])
        for row in db.execute("SELECT left_id, right_id FROM comparisons"):
            marked.update(row)
        if not marked:
            return set()
        holes = ",".join("?" * len(marked))
        rows = db.execute(
            "SELECT path FROM renders WHERE candidate_id IN (%s)" % holes,
            tuple(marked)).fetchall()
        return {row[0] for row in rows}
    except sqlite3.Error:
        return set()
    finally:
        db.close()


def kept_folder(path, place) -> bool:
    for part in path.relative_to(place).parts[:-1]:
        if part.startswith(KEEP_PREFIX):
            return True
    return False


def expired(place, keep_days, now=None, protect=None) -> list[Path]:
    place = Path(str(place))
    if keep_days <= 0 or not place.is_dir():
        return []
    now = time.time() if now is None else now
    limit = now - keep_days * 86400.0
    guarded = {os.path.normcase(os.path.abspath(str(p))) for p in (protect or ())}
    stale = []
    for path in sorted(place.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in CLIP_EXT:
            continue
        if kept_folder(path, place):
            continue
        if os.path.normcase(os.path.abspath(str(path))) in guarded:
            continue
        try:
            if path.stat().st_mtime < limit:
                stale.append(path)
        except OSError:
            continue
    return stale


def sweep(place, keep_days, now=None, report=None, protect=None) -> list[Path]:
    removed = []
    for path in expired(place, keep_days, now, protect):
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
        if report:
            report("удалён по сроку: %s" % path.name)
    return removed
