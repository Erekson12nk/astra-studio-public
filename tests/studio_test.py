# -*- coding: utf-8 -*-
"""Строит окно без mainloop и дёргает логику: вкладки, таймкоды, расшифровка."""
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

from media import build as _build, CACHE as _CACHE

_MEDIA = _build()
_CACHE_U = _CACHE.replace("\\", "/")

import os, sys, time

ROOT = _REPO
SC = _CACHE
sys.path.insert(0, ROOT)

import tkinter.messagebox as _mb
DIALOGS = []
_mb.showerror = lambda *a, **k: DIALOGS.append(("error",) + a)
_mb.showinfo = lambda *a, **k: DIALOGS.append(("info",) + a)

import studio.window as _win
SETTINGS = _win.SETTINGS

from studio.window import Studio, _safe_name, _unique, _number

ok = []


def check(label, value):
    ok.append(bool(value))
    print("  %-52s %s" % (label, "ок" if value else "ПРОВАЛ"))


app = Studio()
app.update()
check("окно построено", app.winfo_exists())
check("две вкладки на месте", app.tab_clips.winfo_exists() and app.tab_rolik.winfo_exists())
check("по умолчанию режим «Ролики»", app.mode == "rolik")
check("страница роликов видна", app.page_rolik.winfo_ismapped())
check("страница клипов скрыта", not app.page_clip.winfo_ismapped())

app._switch("clip")
app.update()
check("переключение на «Клипы»", app.mode == "clip" and app.page_clip.winfo_ismapped())
check("страница роликов спряталась", not app.page_rolik.winfo_ismapped())
app._switch("rolik")
app.update()
check("переключение обратно", app.mode == "rolik" and app.page_rolik.winfo_ismapped())

# источник
app.source.set(os.path.join(SC, "speech.mp4"))
app._refresh_source()
deadline = time.time() + 15
while app.info is None and time.time() < deadline:
    app.update()
    time.sleep(0.05)
check("источник прочитан", app.info is not None and app.info.duration > 0)
check("строка сведений заполнена", "звук" in app.lbl_info["text"])

# таймкоды
app.v_start.set("0:04"); app.v_end.set("0:12"); app.v_title.set("Про рыбалку")
app._add_moment(); app.update()
check("таймкод добавлен", len(app.moments) == 1)
check("в таблице одна строка", len(app.tree.get_children()) == 1)
check("границы разобраны", app.moments[0]["start"] == 4.0 and app.moments[0]["end"] == 12.0)

app.v_start.set("0:20"); app.v_end.set("0:10")
before = len(app.moments)
try:
    app._add_moment()
except Exception:
    pass
check("конец раньше начала отвергнут", len(app.moments) == before)
check("и показана ошибка", any(d[0] == "error" for d in DIALOGS))

# загрузка из файла
tc = os.path.join(SC, "tc_studio.txt")
open(tc, "w", encoding="utf-8").write(
    "# коммент\n0:00:30.000 - 0:01:00.000  Момент из файла\nмусор\n")
app._load_moments_path = tc
import tkinter.filedialog as fd
fd.askopenfilename = lambda **kw: tc
app._load_moments(); app.update()
check("список из файла подхвачен", len(app.moments) == 2)
check("название из файла целое", app.moments[1]["title"] == "Момент из файла")

# расшифровка: кеша нет -> кнопка активна
key = app._transcript_key()
from core import asr
cache = asr.cache_path(app.source.get())
if os.path.exists(cache):
    os.remove(cache)
app._refresh_transcript(); app.update()
check("без кеша кнопка «Расшифровать» активна", app.btn_speech.enabled)
check("подпись говорит что расшифровки нет", "нет" in app.lbl_speech["text"])

asr.save_cache(app.source.get(), key, asr.Transcript(
    segments=[asr.Segment(idx=0, start=0, end=1, text="hi",
                          words=[asr.Word(0, 1, "hi")])]))
app._refresh_transcript(); app.update()
check("с кешем кнопка гаснет", not app.btn_speech.enabled)
check("подпись говорит что готово", "готова" in app.lbl_speech["text"])

# журнал и прогресс
app._say("проверка журнала")
check("журнал пишется", "проверка журнала" in app.log.get("1.0", "end"))
app._on_event("progress", (0.5, ""))
app.update()
check("прогресс двигается", abs(app.progress["value"] - 500) < 1)
app._on_event("stage", "этап")
check("этап показан", app.lbl_stage["text"] == "этап")

# вспомогательные
check("имя файла чистится", _safe_name("Про: рыбалку/2") == "Про__рыбалку_2")
check("пустое имя -> пусто", _safe_name("  ") == "")
check("число с запятой", _number("1,5", 0) == 1.5)
check("мусор -> запасное", _number("абв", 7) == 7)

# кнопки поиска не уезжают за край, даже когда окно низкое
import time as _time


def _labels(widget, texts):
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == "Label" and str(child.cget("text")) in texts:
            found.append(child)
        found += _labels(child, texts)
    return found


app.geometry("1180x700")
app._switch("clip")
app._switch_clip("search")
_end = _time.time() + 0.8
while _time.time() < _end:
    app.update()
    _time.sleep(0.01)
_wanted = ("Передать моменты", "Сохранить список")
_shown = _labels(app.clip_search, _wanted)
check("кнопки «Передать моменты» и «Сохранить список» видны",
      len(_shown) == 2 and all(button.winfo_ismapped() and button.winfo_rooty()
                               + button.winfo_height() <= app.log_wrap.winfo_rooty()
                               for button in _shown))

app._remember()
check("настройки сохранены", os.path.isfile(SETTINGS))

app.destroy()
print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
