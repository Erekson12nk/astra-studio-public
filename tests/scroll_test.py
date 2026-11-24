# -*- coding: utf-8 -*-
"""Содержимое прокручивается, и окно больше не распирает по высоте.

Окно не влезало в свою высоту: требовалось 1046 px при 940, и всякая новая
строка молча выдавливала журнал за край. Теперь страница сборки лежит
в прокрутке. Здесь проверяется и сам виджет, и то, ради чего он заведён.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os
import sys
import tempfile
import tkinter as tk

os.environ["ASTRA_STUDIO_SETTINGS"] = os.path.join(
    tempfile.mkdtemp(prefix="scroll_cfg_"), "settings.json")

import tkinter.messagebox as _mb
_mb.showerror = lambda *a, **k: None
_mb.showinfo = lambda *a, **k: None

from studio import theme
from studio.window import Studio

ok = []


def check(label, value):
    ok.append(bool(value))
    print("  %-56s %s" % (label, "ок" if value else "ПРОВАЛ"))


root = tk.Tk()
theme.apply(root)
root.geometry("400x300")
box = theme.Scrolled(root)
box.pack(fill="both", expand=True)
tk.Frame(box.body, height=100, width=200, background=theme.PANEL).pack()
root.update()

check("полосы нет, пока всё влезает", not box.shown)
check("влезающее так и считается влезающим", box.fits())

tall = tk.Frame(box.body, height=900, width=200, background=theme.PANEL)
tall.pack()
root.update()
check("полоса появилась, когда переросло", box.shown)
check("переросшее видно по мерке", not box.fits())

start = box.canvas.canvasy(0)
box._wheel(type("Колесо", (), {"delta": -120})())
root.update()
check("колесо крутит вниз", box.canvas.canvasy(0) > start)
box._wheel(type("Колесо", (), {"delta": 120})())
root.update()
check("и обратно вверх", box.canvas.canvasy(0) <= start + 1)

tall.destroy()
root.update()
check("полоса убралась, когда стало влезать", not box.shown)
check("и вид вернулся к началу", box.canvas.canvasy(0) == 0)
root.destroy()

app = Studio()
app.update()
app._switch("rolik")
app._switch_rolik("build")
app.update()

check("страница сборки прокручиваемая",
      isinstance(app.rolik_build, theme.Scrolled))
check("окно больше не распирает",
      app.winfo_reqheight() <= app.winfo_height())
check("журнал на месте", app.log_wrap.winfo_ismapped())

before = app.winfo_reqheight()
app.fine.toggle()
app.update()
check("раскрытые настройки не растят окно",
      app.winfo_reqheight() == before)
check("журнал остался и с раскрытыми", app.log_wrap.winfo_ismapped())
check("кнопка сборки доступна", app.btn_build.winfo_exists())

app.destroy()
print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
