# -*- coding: utf-8 -*-
"""Оформление: палитра, иконки по тексту кнопок, анимации переходов."""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _REPO)

import tempfile
import time
import tkinter as tk

_os.environ["ASTRA_STUDIO_SETTINGS"] = _os.path.join(tempfile.mkdtemp(), "s.json")

from core import ui
from studio import icons, motion, theme

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


print("палитра")
check("киберпанк: жёлтый акцент, тёмный текст на нём", ui.ACCENT == "#fcee0a"
      and ui.ON_ACCENT == "#07070c")
check("выбора стиля нет", not hasattr(ui, "STYLES") and not hasattr(ui, "use_style"))

print("иконки")
check("кнопки узнаются по тексту", icons.name_for("Скачать") == "download"
      and icons.name_for("Выбрать…") == "folder" and icons.name_for("▶ Прослушать") == "play"
      and icons.name_for("Свернуть в трей") == "tray" and icons.name_for("Голос и донаты") == "mic")
check("незнакомый текст — без иконки", icons.name_for("Это что-то") == "")
check("значок ▶ уходит из текста", icons.bare_text("▶ Прослушать") == "Прослушать")
drawn = {name: icons.draw(name, "#ffffff", 16) for name in icons.SHAPES}
check("все иконки рисуются", all(image is not None and image.size == (16, 16)
                                 for image in drawn.values()))
check("иконка не пустая", all(image.getbbox() for image in drawn.values()),
      [name for name, image in drawn.items() if not image.getbbox()])
check("знак Студии рисуется", icons.logo(32).getbbox())

print("анимации")
check("сглаживание: с нуля до единицы", motion.ease(0) == 0 and motion.ease(1) == 1
      and 0.5 < motion.ease(0.5) < 1)
check("смешение цветов", motion.blend("#000000", "#ffffff", 0.5) == "#808080"
      and motion.blend("#102030", "#102030", 0.3) == "#102030")

root = tk.Tk()
root.geometry("600x400")
ui.apply(root)


def spin(seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


button = theme.Button(root, "Скачать", lambda: None, primary=True)
button.pack()
check("у кнопки иконка, текст без значков", bool(button.cget("image"))
      and button.cget("text") == "Скачать")
button.set_enabled(False)
off = button.cget("image")
button.set_enabled(True)
check("выключенная кнопка — серая иконка", off != button.cget("image"))
button._enter()
spin(0.3)
check("наведение доводит цвет до подсветки", button.cget("background") == button.hover)
button._leave()
spin(0.35)
check("увод мыши возвращает цвет", button.cget("background") == button.rest)

bar = tk.Frame(root)
bar.pack(fill="x")
pages = tk.Frame(root)
pages.pack(fill="both", expand=True)
first = tk.Frame(pages, background="#ff0000")
second = tk.Frame(pages, background="#00ff00")
tab_a = theme.Tab(bar, "Клипы", lambda: None)
tab_a.pack(side="left")
tab_b = theme.Tab(bar, "Ролики", lambda: None)
tab_b.pack(side="left")
tab_a.set_active(True)
first.pack(fill="both", expand=True)
spin(0.2)
line = bar._astra_indicator
check("полоска встала под первую вкладку", line.x == tab_a.winfo_x())
tab_a.set_active(False)
tab_b.set_active(True)
first.pack_forget()
second.pack(fill="both", expand=True)
motion.reveal(second)
root.update()
check("страница въезжает: на время анимации — place", second.winfo_manager() == "place")
spin(0.1)
check("полоска в пути", tab_a.winfo_x() < line.x < tab_b.winfo_x(), (line.x, tab_b.winfo_x()))
spin(0.4)
check("доехала: страница снова в pack", second.winfo_manager() == "pack")
check("полоска под второй вкладкой", line.x == tab_b.winfo_x()
      and line.width == tab_b.winfo_width())
second.pack_forget()
first.pack(fill="both", expand=True)
motion.reveal(first)
root.update()
second.pack(fill="both", expand=True)
first.pack_forget()
motion.reveal(second)
spin(0.4)
check("быстрое переключение — одна страница", second.winfo_manager() == "pack"
      and first.winfo_manager() == "")
_os.environ["ASTRA_MOTION"] = "0"
second.pack_forget()
first.pack(fill="both", expand=True)
motion.reveal(first)
check("анимации выключены — сразу на месте", first.winfo_manager() == "pack")
del _os.environ["ASTRA_MOTION"]

print("первые окна сворачиваются")
from studio import launcher

chooser = launcher.Launcher(root, {})
root.update()
check("окно выбора режима не перехватывает ввод", chooser.grab_current() is None)
check("и не привязано к скрытому главному окну", not str(chooser.wm_transient()))
if _sys.platform == "win32":
    import ctypes

    handle = ctypes.windll.user32.GetParent(chooser.winfo_id())
    ctypes.windll.user32.PostMessageW(handle, 0x0112, 0xF020, 0)   # кнопка «свернуть»
    spin(0.6)
    check("кнопка «свернуть» его сворачивает", chooser.state() == "iconic", chooser.state())
chooser.destroy()
root.destroy()

print("\nитог: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
