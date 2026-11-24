# -*- coding: utf-8 -*-
"""Первое окно: профиль стримера.

При первом запуске Студия знакомится: заводит профиль стримера со снимком с вебки.
Настоящие профили не трогаются: папка профилей уводится во временную.
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

WORK = tempfile.mkdtemp(prefix="welcome_")
os.environ["ASTRA_PROFILES"] = os.path.join(WORK, "profiles")
os.environ["ASTRA_STUDIO_SETTINGS"] = os.path.join(WORK, "settings.json")

import tkinter as tk
import tkinter.messagebox as _mb
_mb.showerror = lambda *a, **k: None
_mb.showinfo = lambda *a, **k: None

from core import profiles as _pro
from studio import welcome as _welcome

ok = []


def check(label, value):
    ok.append(bool(value))
    print("  %-58s %s" % (label, "ок" if value else "ПРОВАЛ"))


photo = os.path.join(WORK, "лицо.png")
open(photo, "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

root = tk.Tk()
root.withdraw()

must, reason = _welcome.needed()
check("без профиля окно нужно", must)

window = _welcome.Welcome(root, reason)
root.update()

window._accept()
check("без имени не пускает", "Впишите имя" in window.lbl["text"])

window.v_name.set("Стример")
window._accept()
check("без снимка новый профиль не заводится",
      "снимок" in window.lbl["text"].lower())

window.v_photo.set(photo)
window._accept()
check("окно закрылось с успехом", window.done)
check("профиль заведён", _pro.names() == ["Стример"])
check("профиль стал активным", _pro.active_name() == "Стример")
check("снимок лёг в профиль", _pro.face_path(_pro.active()) is not None)

must, _reason = _welcome.needed()
check("во второй раз окно не нужно", not must)

second = _welcome.Welcome(root, "")
second.v_name.set("Стример")
second._accept()
check("для готового профиля снимок не спрашивают", second.done)

root.destroy()
shutil.rmtree(WORK, ignore_errors=True)
print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
