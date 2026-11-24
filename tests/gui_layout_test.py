# -*- coding: utf-8 -*-
"""Окно astra: раскладка «Сама решит» и покраска выпадающих списков."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os, sys

ROOT = _REPO
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "astra"))

import tkinter as tk
from tkinter import ttk

import gui as G

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


window = G.App()
window.withdraw()
window.update()
app = window.editor
app.source = os.path.join(ROOT, "нет-такого.mp4")

# --- «Сама решит» есть в списке и стоит первой ---
check("«Сама решит» первая в списке видов", G.LAYOUT_NAMES[0] == "Сама решит",
      G.LAYOUT_NAMES[0])
check("ей отвечает раскладка auto", G.LAYOUT_CODES["Сама решит"] == "auto")


def command_for(name):
    app.var_layout.set(name)
    return app._build_command([(1.0, 5.0)], os.path.join(ROOT, "выход.mp4"))


# --- при «Сама решит» окно не прибивает ни рамку, ни субтитры ---
auto = command_for("Сама решит")
check("auto уходит в движок", "auto" in auto, " ".join(auto[-14:])[:60])
check("рамка вебки не навязывается", "--cam-box" not in auto)
check("положение субтитров не навязывается", "--subs-y" not in auto)

# --- при явной раскладке всё как раньше ---
fixed = command_for("Вебка и экран")
check("при явном виде рамка передаётся", "--cam-box" in fixed)
check("при явном виде субтитры передаются", "--subs-y" in fixed)
check("вид передаётся как split", "split" in fixed)

# --- если субтитры двигали мышью, их уважают и в auto ---
app.subs_moved = True
moved = command_for("Сама решит")
check("сдвинутые субтитры переживают auto", "--subs-y" in moved)
app.subs_moved = False

# --- модель распознавания берётся не из воздуха ---
check("в списке моделей есть turbo",
      G.ave.DEFAULT_WHISPER == "large-v3-turbo", G.ave.DEFAULT_WHISPER)

# --- выпадающие списки покрашены, а не чёрным по чёрному ---
style = ttk.Style(window)
readonly = dict(style.map("TCombobox", "selectforeground"))
field = dict(style.map("TCombobox", "selectbackground"))
check("текст в списке не цвета выделения", "readonly" in readonly, readonly)
check("фон списка задан отдельно", "readonly" in field, field)
check("цвет текста и фона списка разные",
      readonly.get("readonly") != field.get("readonly"),
      "%s / %s" % (readonly.get("readonly"), field.get("readonly")))
window.destroy()

# --- то же самое в окне Студии ---
from studio import theme as T

root = tk.Tk()
root.withdraw()
T.apply(root)
style = ttk.Style(root)
ro = dict(style.map("TCombobox", "selectforeground"))
bg = dict(style.map("TCombobox", "selectbackground"))
check("Студия: текст списка задан", ro.get("readonly") == T.TEXT, ro.get("readonly"))
check("Студия: фон списка задан", bg.get("readonly") == T.PANEL2, bg.get("readonly"))
check("Студия: текст и фон не совпадают", ro.get("readonly") != bg.get("readonly"))
root.destroy()

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
