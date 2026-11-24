# -*- coding: utf-8 -*-
"""Цензура внутри Студии: вкладка, подхват готового ролика, автономное окно."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)
import tempfile as _tempfile
_os.environ.setdefault("ASTRA_STUDIO_SETTINGS", _os.path.join(
    _tempfile.gettempdir(), "astra_studio_tests", "settings.json"))

from media import build as _build

_MEDIA = _build()

import os, re, shutil, sys, tempfile, time

ROOT = _REPO
sys.path.insert(0, ROOT)

import tkinter.messagebox as _mb
DIALOGS = []
_mb.showerror = lambda *a, **k: DIALOGS.append(("error",) + a)
_mb.showinfo = lambda *a, **k: DIALOGS.append(("info",) + a)
_mb.showwarning = lambda *a, **k: DIALOGS.append(("warning",) + a)

import tkinter as tk

from core import ui
from studio.window import Studio

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


def read(name):
    with open(_os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


SOURCE = read("audioclean/gui.py")

# --- окно перестало быть окном ---
check("панель — обычная рамка, а не окно",
      "class Cleaner(tk.Frame):" in SOURCE)
check("оболочка для автономного запуска осталась",
      "class App(tk.Tk):" in SOURCE)
check("панель не трогает заголовок и размер окна",
      "self.master.title" not in SOURCE and "self.master.geometry" not in SOURCE)
check("своей темы больше не ставит",
      "vista" not in SOURCE and "theme_use" not in SOURCE)
check("умеет сказать, что занята, и закрыться",
      "def busy(self)" in SOURCE and "def shutdown(self)" in SOURCE)

# --- цвета взяты из общей темы, а не вбиты числами ---
body = SOURCE[SOURCE.index("class Cleaner"):]
hardcoded = sorted(set(re.findall(r'"#[0-9a-fA-F]{6}"', body)))
check("в панели не осталось вбитых цветов", not hardcoded, hardcoded)

# --- вкладка в Студии ---
work = tempfile.mkdtemp(prefix="clean_test_")
rolik = _os.path.join(work, "Готовый ролик.mp4")
shutil.copyfile(_MEDIA["speech"], rolik)

app = Studio()
app.source.set(_MEDIA["speech"])
app.v_outdir.set(work)
app.update()

check("цензура не строится, пока её не открыли", app.cleaner is None)
check("режим «Ролики» открылся на сборке", app.rolik_page == "build")

app._switch("rolik")
app._switch_rolik("clean")
app.update()

check("панель цензуры построена", app.cleaner is not None)
check("она лежит на фоне Студии",
      app.cleaner is not None and app.cleaner.cget("background") == ui.BG)
check("во встроенном виде без оболочки",
      app.cleaner is not None and app.cleaner.chrome is False)
check("готовый ролик подхвачен сам",
      app.cleaner is not None
      and _os.path.basename(app.cleaner.v_video.get()) == "Готовый ролик.mp4",
      app.cleaner.v_video.get() if app.cleaner else "")
check("имя для результата подставлено",
      app.cleaner is not None and app.cleaner.v_out.get().endswith("_clean.mp4"),
      app.cleaner.v_out.get() if app.cleaner else "")
check("панель ничем не занята", app.cleaner is not None and not app.cleaner.busy())

# --- журнал Студии на этой вкладке не мешает: у панели свой ---
check("общий журнал спрятан", not app.log_wrap.winfo_ismapped())
app._switch_rolik("build")
app.update()
check("и возвращается на сборке", app.log_wrap.winfo_ismapped())

# --- второй заход не строит панель заново ---
was = app.cleaner
app._switch_rolik("clean")
app.update()
check("панель строится один раз", app.cleaner is was)

# --- выбранный руками файл автоподстановкой не затирается ---
app.cleaner.v_video.set("свой.mp4")
app._switch_rolik("build")
app._switch_rolik("clean")
app.update()
check("свой выбор не затирается", app.cleaner.v_video.get() == "свой.mp4")

app._close()

# --- автономное окно всё ещё собирается ---
from audioclean.gui import App as CleanApp

alone = CleanApp()
alone.update()
check("автономное окно собирается", alone.cleaner is not None)
check("в нём заголовок на месте", "AudioClean" in alone.title())
alone.cleaner.shutdown()
alone.destroy()

shutil.rmtree(work, ignore_errors=True)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
