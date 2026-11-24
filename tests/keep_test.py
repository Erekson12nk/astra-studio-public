# -*- coding: utf-8 -*-
"""Срок хранения роликов: выбор в окне, подметание после сборки.

Ролики копятся на диске, и нужен был таймер: по умолчанию три дня,
срок выбирается. Здесь проверяется и перевод «3 дня» в число и обратно,
и само подметание — что оно сносит старое, щадит свежее, только что
собранное и папки на «_».
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os
import shutil
import sys
import tempfile
import time

os.environ["ASTRA_STUDIO_SETTINGS"] = os.path.join(
    tempfile.mkdtemp(prefix="keep_cfg_"), "settings.json")

import tkinter.messagebox as _mb
_mb.showerror = lambda *a, **k: None
_mb.showinfo = lambda *a, **k: None

from core import profiles as _pro
from studio.window import Studio, KEEP_CHOICES, keep_days, keep_title

ok = []


def check(label, value):
    ok.append(bool(value))
    print("  %-56s %s" % (label, "ок" if value else "ПРОВАЛ"))


check("по умолчанию три дня", _pro.DEFAULT_KEEP_DAYS == 3)
check("«3 дня» это 3", keep_days("3 дня") == 3)
check("«не удалять» это 0", keep_days("не удалять") == 0)
check("3 переводится в «3 дня»", keep_title(3) == "3 дня")
check("0 переводится в «не удалять»", keep_title(0) == "не удалять")
check("мусор в настройках не роняет окно", keep_title("абв") == "3 дня")
check("все варианты ходят туда и обратно",
      all(keep_days(title) == value for value, title in KEEP_CHOICES))

work = tempfile.mkdtemp(prefix="keep_")
old = time.time() - 10 * 86400
kept_dir = os.path.join(work, "_мои")
os.makedirs(kept_dir)


def put(path, when=None):
    with open(path, "wb") as handle:
        handle.write(b"\0" * 2048)
    if when:
        os.utime(path, (when, when))
    return path


stale = put(os.path.join(work, "старый.mp4"), old)
fresh = put(os.path.join(work, "свежий.mp4"))
guarded = put(os.path.join(kept_dir, "беречь.mp4"), old)
spared = put(os.path.join(work, "только что собран.mp4"), old)
alien = put(os.path.join(work, "заметки.txt"), old)

gone = _pro.sweep(work, 3, protect={os.path.abspath(spared)})
names = {os.path.basename(str(p)) for p in gone}

check("старый ролик удалён", names == {"старый.mp4"})
check("свежий на месте", os.path.isfile(fresh))
check("папка на «_» не тронута", os.path.isfile(guarded))
check("только что собранный не тронут", os.path.isfile(spared))
check("чужие файлы не трогаются", os.path.isfile(alien))

put(os.path.join(work, "ещё один старый.mp4"), old)
check("нулевой срок не удаляет ничего", _pro.sweep(work, 0) == [])

app = Studio()
app.update()
check("в окне есть выбор срока", hasattr(app, "v_keep"))
check("окно открывается на трёх днях", app.v_keep.get() == "3 дня")
app.v_keep.set("14 дней")
app._remember()
check("выбор сохраняется", app.cfg.get("keep_days") == 14)
app.destroy()

shutil.rmtree(work, ignore_errors=True)
print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
