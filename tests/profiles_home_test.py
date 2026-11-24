# -*- coding: utf-8 -*-
"""Профили живут в папке программы: клипы, оценки и сравнения — внутри профиля."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

WORK = tempfile.mkdtemp()
HOME = Path(WORK) / "программа"
HOME.mkdir()
_os.environ["ASTRA_PROFILES"] = str(HOME / "profiles")

from core import profiles as _pro

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-58s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


def photo(name):
    path = Path(WORK) / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(path)


print("\n--- где лежат профили ---")
del _os.environ["ASTRA_PROFILES"]
check("без переменной корень профилей — папка программы",
      _pro.root() == _pro.app_root() / "profiles", _pro.root())
check("папка программы — это корень репозитория",
      (_pro.app_root() / "core" / "profiles.py").is_file())
_os.environ["ASTRA_PROFILES"] = str(HOME / "profiles")
check("переменная ASTRA_PROFILES перебивает", _pro.root() == HOME / "profiles")

print("\n--- несколько профилей рядом ---")
first = _pro.create("Первый", photo("a.png"))
second = _pro.create("Второй", photo("b.png"), keep_days=7)
check("оба профиля видны", _pro.names() == ["Второй", "Первый"], _pro.names())
check("у каждого своя папка", first["folder"] != second["folder"])
check("папка клипов внутри профиля",
      _pro.clips_dir(first) == Path(first["folder"]) / "clips")
check("папка клипов создана сразу", _pro.clips_dir(first).is_dir())
check("база оценок внутри профиля",
      _pro.db_path(second) == Path(second["folder"]) / "clips.db")
check("базы у профилей разные", _pro.db_path(first) != _pro.db_path(second))
_pro.set_active("Второй")
check("активный переключается", _pro.active_name() == "Второй")
check("активный профиль читается целиком",
      (_pro.active() or {}).get("keep_days") == 7)
check("своя папка для клипов перебивает общую",
      _pro.clips_dir({"folder": str(HOME), "out_dir": r"D:\мои клипы"})
      == Path(r"D:\мои клипы"))

print("\n--- движок берёт пути из профиля ---")
from clipper.cli import apply_profile
from clipper.config import Config


class Args:
    profile = "Первый"
    no_profile = False


cfg = Config()
taken = apply_profile(Args(), cfg)
check("профиль применился", (taken or {}).get("name") == "Первый")
check("клипы пойдут в папку профиля", cfg.out_dir == _pro.clips_dir(first))
check("база — тоже в папке профиля", cfg.db_path == _pro.db_path(first))
other = Config()


class Args2(Args):
    profile = "Второй"


apply_profile(Args2(), other)
check("у второго профиля своя база", other.db_path != cfg.db_path)
check("без профиля база остаётся общей",
      Config().db_path == Config().work_dir / "clipper.sqlite3")

print("\n--- окно: переключатель и создание ---")
_os.environ["ASTRA_STUDIO_SETTINGS"] = str(Path(WORK) / "settings.json")
from studio.window import Studio
from studio import profiles_ui

app = Studio()
app.withdraw()
app.update()
check("оба профиля в списке окна",
      set(app.pick_profile.cget("values")) == {"Первый", "Второй"})
check("выбран активный", app.v_profile.get() == "Второй")
check("сравнение по умолчанию включено", bool(app.v_compare.get()))
window_cfg = app._clipper_config()
check("окно кладёт клипы в папку активного профиля",
      window_cfg.out_dir == _pro.clips_dir(second))
check("окно пишет оценки в базу активного профиля",
      window_cfg.db_path == _pro.db_path(second))
app.v_compare.set(False)
check("галочка выключает сравнение", not app._clipper_config().compare)

maker = profiles_ui.Maker(app)
maker.v_name.set("Третий")
maker.v_photo.set(photo("c.png"))
maker.v_keep.set("7 дней")
maker._accept()
app.update()
app._refresh_profiles()
check("профиль создан из окна", "Третий" in _pro.names())
check("новый профиль стал активным", _pro.active_name() == "Третий")
check("список в окне обновился", "Третий" in app.pick_profile.cget("values"))
app._profile_changed("Третий")
check("база сменилась вместе с профилем",
      app._clipper_config().db_path == _pro.db_path(_pro.active()))
app.destroy()

print("\n--- переезд со старого места ---")
del _os.environ["ASTRA_PROFILES"]
OLD = Path(WORK) / "старое" / "profiles"
NEW = Path(WORK) / "новое" / "profiles"
(OLD / "Прежний").mkdir(parents=True)
(OLD / "Прежний" / "profile.json").write_text(
    '{"name": "Прежний", "keep_days": 3}', encoding="utf-8")
(OLD / "active.txt").write_text("Прежний", encoding="utf-8")
old_db = Path(WORK) / "старая-работа" / "clipper.sqlite3"
old_db.parent.mkdir(parents=True)
sqlite3.connect(old_db).close()
_os.environ["CLIPPER_WORK"] = str(old_db.parent)
_pro.root = lambda: NEW
_pro.former_root = lambda: OLD
moved = _pro.migrate()
check("профиль переехал в папку программы", moved == ["Прежний"], moved)
check("на старом месте его больше нет", not (OLD / "Прежний").is_dir())
check("активный профиль переехал вместе с ним",
      _pro.active_name() == "Прежний")
check("прежние оценки и сравнения легли в профиль",
      (NEW / "Прежний" / "clips.db").is_file())
check("повторный переезд ничего не делает", _pro.migrate() == [])

shutil.rmtree(WORK, ignore_errors=True)
print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
