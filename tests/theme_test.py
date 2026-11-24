# -*- coding: utf-8 -*-
"""Тема одна на оба окна: палитра не раздваивается, стили не теряются."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import re
import sys
import tkinter as tk
from tkinter import ttk

ROOT = _REPO
sys.path.insert(0, ROOT)
sys.path.insert(0, _os.path.join(ROOT, "astra"))

from core import ui

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


def read(name):
    with open(_os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


SOURCES = {name: read(name) for name in
           ("astra/gui.py", "studio/theme.py", "studio/window.py",
            "clipper/gui.py", "audioclean/gui.py")}

# --- палитра объявлена ровно в одном месте ---
palette = re.compile(r'^(BG|PANEL|PANEL2|LINE|TEXT|DIM|ACCENT|ACCENT2|SOFT|OK|BAD|'
                     r'WARN|UI_FONT|SMALL_FONT|BIG_FONT|MONO_FONT|MICRO_FONT)\s*=',
                     re.M)
for name, text in SOURCES.items():
    check("палитра не объявляется в %s" % name, not palette.search(text),
          palette.findall(text))
check("палитра объявлена в core/ui.py", len(palette.findall(read("core/ui.py"))) >= 16)

# --- apply один на всех ---
applies = [name for name, text in SOURCES.items()
           if re.search(r"^def apply(_theme)?\(", text, re.M)]
check("своей apply ни у кого не осталось", not applies, applies)
check("studio.theme.apply — это core.ui.apply",
      __import__("studio.theme", fromlist=["theme"]).apply is ui.apply)

# --- каждый стиль, который просят по имени, тема действительно задаёт ---
root = tk.Tk()
root.withdraw()
style = ui.apply(root)

asked = set()
for text in SOURCES.values():
    asked.update(re.findall(r'style="([A-Za-z][A-Za-z0-9.]*)"', text))
for name in ("TFrame", "TLabel", "TButton", "TEntry", "TCombobox", "TSpinbox",
             "TCheckbutton", "TRadiobutton", "Treeview", "TNotebook",
             "Horizontal.TScale", "TScrollbar", "TProgressbar", "TLabelframe",
             "TSeparator", "TPanedwindow"):
    asked.add(name)

lost = sorted(n for n in asked if not style.configure(n))
check("стилей без описания в теме нет", not lost, lost)

# --- поверхности не путаются: BG для страницы, PANEL для карточки ---
surface = [
    ("TLabel", ui.BG), ("Dim.TLabel", ui.BG), ("Head.TLabel", ui.BG),
    ("Ok.TLabel", ui.BG), ("Bad.TLabel", ui.BG),
    ("Panel.TFrame", ui.PANEL), ("Panel.TLabel", ui.PANEL),
    ("PanelDim.TLabel", ui.PANEL), ("PanelOk.TLabel", ui.PANEL),
    ("PanelBad.TLabel", ui.PANEL),
]
for name, want in surface:
    got = str(style.lookup(name, "background"))
    check("%s лежит на %s" % (name, want), got == want, got)

check("галки и переключатели на карточке",
      style.lookup("TCheckbutton", "background") == ui.PANEL
      and style.lookup("TRadiobutton", "background") == ui.PANEL)

# --- то, что раньше знала только astra, знает и Студия ---
check("тонкая полоса прогресса осталась тонкой",
      str(style.lookup("Thin.Horizontal.TProgressbar", "thickness")) == "4",
      style.lookup("Thin.Horizontal.TProgressbar", "thickness"))
check("полосы прокрутки покрашены",
      style.lookup("TScrollbar", "background") == ui.PANEL2)

# --- список в состоянии readonly рисуется цветами выделения ---
readonly = dict(style.map("TCombobox", "selectforeground"))
check("readonly-список читается", readonly.get("readonly") == ui.TEXT, readonly)

# --- окно astra строится под общей темой ---
import gui as agui

check("astra берёт цвета из ядра", agui.PANEL is ui.PANEL and agui.BG is ui.BG)
check("astra.apply_theme — это core.ui.apply", agui.apply_theme is ui.apply)
check("round_rect живёт в ядре", agui.round_rect is ui.round_rect)

editor = agui.Editor(root, chrome=False)
editor.update_idletasks()
check("монтаж строится под общей темой", editor.winfo_exists())
check("фон монтажа — фон Студии", editor.cget("background") == ui.BG)
editor.shutdown()
root.destroy()

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
