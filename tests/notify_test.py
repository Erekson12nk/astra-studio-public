# -*- coding: utf-8 -*-
"""Долгая работа видна и без окна: процент в заголовке, мигание по концу.

Скачивание стрима идёт часами, и сидеть перед окном никто не будет.
Здесь проверяется, что процент уходит в заголовок, что по завершении
заголовок возвращается на место, и что мигание зовётся только для долгой
работы и только когда на окно не смотрят.
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
import time

os.environ["ASTRA_STUDIO_SETTINGS"] = os.path.join(
    tempfile.mkdtemp(prefix="notify_cfg_"), "settings.json")

import tkinter.messagebox as _mb
_mb.showerror = lambda *a, **k: None
_mb.showinfo = lambda *a, **k: None

from core import ui as _ui
from studio.window import Studio, TITLE, LONG_TASK

ok = []


def check(label, value):
    ok.append(bool(value))
    print("  %-56s %s" % (label, "ок" if value else "ПРОВАЛ"))


app = Studio()
app.update()

check("заголовок обычный, пока тихо", app.title() == TITLE)

blinks = []
_ui.attention = lambda window, sound=True: blinks.append(time.time()) or True
_ui.watching = lambda window: False

app.runner.started = time.time() - 5 * 60
app.runner.thread = type("Живой", (), {"is_alive": staticmethod(lambda: True)})()
app._on_event("progress", (0.215, "3.1 МБ/с · осталось 1 ч 48 мин"))
app.update()
check("процент виден в заголовке", app.title() == "%s — 21%%" % TITLE)
check("подробности ушли в строку состояния",
      "3.1 МБ/с" in app.lbl_stage["text"])

app.runner.thread = None
app._on_event("done", ["готово"])
app.update()
check("заголовок вернулся", app.title() == TITLE)
check("по долгой работе мигнули", len(blinks) == 1)
check("и сказали об этом в журнале",
      "мигнуло" in app.log.get("1.0", "end"))

blinks.clear()
app.runner.started = time.time() - 3
app._on_event("done", ["готово"])
check("по быстрой работе не мигаем", not blinks)

blinks.clear()
app.runner.started = time.time() - 5 * 60
_ui.watching = lambda window: True
app._on_event("done", ["готово"])
check("когда смотрят на окно, не мигаем", not blinks)

blinks.clear()
_ui.watching = lambda window: False
app.runner.started = 0.0
app._on_event("error", "беда")
check("без отметки времени не мигаем", not blinks)

app.runner.started = time.time() - 5 * 60
app._on_event("error", "беда")
check("после ошибки тоже зовём, работа ведь кончилась", len(blinks) == 1)

check("порог долгой работы разумный", 30 <= LONG_TASK <= 300)

app.destroy()
print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
