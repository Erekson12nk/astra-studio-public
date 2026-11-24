# -*- coding: utf-8 -*-
"""Тонкие настройки убраны под раскрывашку, но продолжают работать.

Заказчик просил окно попроще. Технические поля — громкость, затемнение,
размытие, crf, видеокарта — свёрнуты. Здесь проверяется, что они никуда
не делись, что их значения по-прежнему уходят в сборку, что раскрывашка
помнит своё состояние и что журнал не выдавило за край окна.
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

os.environ["ASTRA_STUDIO_SETTINGS"] = os.path.join(
    tempfile.mkdtemp(prefix="fold_cfg_"), "settings.json")

import tkinter.messagebox as _mb
_mb.showerror = lambda *a, **k: None
_mb.showinfo = lambda *a, **k: None

from studio.window import Studio

ok = []


def check(label, value):
    ok.append(bool(value))
    print("  %-56s %s" % (label, "ок" if value else "ПРОВАЛ"))


app = Studio()
app.update()

check("раскрывашка есть", hasattr(app, "fine"))
check("свёрнута при первом запуске", not app.fine.opened)
check("свёрнутое тело не показано", not app.fine.body.winfo_ismapped())
check("в заголовке видно, что раскрывается", "Тонкие настройки" in app.fine.head["text"])

check("поля живы и свёрнутыми", all(
    var.get() != "" for var in (app.v_vol, app.v_fade, app.v_blur, app.v_crf)))
check("качество по умолчанию 20", app.v_crf.get() == "20")

app.fine.toggle()
app.update()
check("раскрывается", app.fine.opened and app.fine.body.winfo_ismapped())
app.fine.toggle()
app.update()
check("сворачивается обратно", not app.fine.body.winfo_ismapped())

app.fine.toggle()
app.v_crf.set("28")
app._remember()
check("состояние раскрывашки запомнено", app.cfg.get("fine_open") is True)
check("значение из свёрнутого поля сохраняется", app.cfg.get("crf") == "28")

check("журнал не выдавлен за край", app.log_wrap.winfo_ismapped())
app.update()
check("и не выдавлен при раскрытых настройках",
      app.fine.opened and app.log_wrap.winfo_ismapped())
check("кнопка сборки на месте", app.btn_build.winfo_ismapped())
check("выбор срока хранения на виду", app.v_keep.get() != "")

app.destroy()
print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
