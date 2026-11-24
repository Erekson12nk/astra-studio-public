# -*- coding: utf-8 -*-
"""Отбор по смыслу через окно: движок heuristic, общая расшифровка."""
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

import os, shutil, sys, tempfile, time

ROOT = _REPO
SC = _CACHE
sys.path.insert(0, ROOT)

from core import asr, ffmpeg as FF

calls = {"n": 0}
_real = asr.transcribe


def counted(*a, **kw):
    calls["n"] += 1
    return _real(*a, **kw)


asr.transcribe = counted

import tkinter.messagebox as _mb
ERRORS = []
_mb.showerror = lambda *a, **k: ERRORS.append(a)
_mb.showinfo = lambda *a, **k: None

from studio.window import Studio

work = tempfile.mkdtemp()
stream = os.path.join(work, "stream.mp4")
shutil.copy2(os.path.join(SC, "long.mp4"), stream)

os.environ["CLIPPER_WORK"] = os.path.join(work, "clipper")
os.environ["CLIPPER_OUT"] = os.path.join(work, "clipper", "out")

app = Studio()
app.source.set(stream)
app.model.set("tiny")
app.language.set("en")
app._refresh_source()
app.v_backend.set("heuristic")
app.v_count.set("5")
app.v_min.set("15")
app.v_max.set("45")
app._backend_hint()
app.update()

print("  подсказка про движок:", app.lbl_backend["text"][:60])
app._check_backend()
app.update()
print("  проверка движка     :", app.lbl_backend["text"][:60])

# 1. расшифровки нет — отбор по смыслу должен её сделать
print("\n--- первый прогон (расшифровки нет) ---")
t0 = time.time()
app._scan_sense()
deadline = time.time() + 600
while app.runner.busy() and time.time() < deadline:
    app.update(); time.sleep(0.05)
for _ in range(30):
    app.update(); time.sleep(0.05)
first = calls["n"]
print("  обращений к whisper : %d, за %.1f с" % (first, time.time() - t0))
print("  найдено моментов    : %d" % len(getattr(app, "clip_moments", [])))
for m in getattr(app, "clip_moments", [])[:3]:
    print("    %7.1f-%7.1f  %4.1f  %s" % (m["start"], m["end"], m["score"], m["title"][:40]))
print("  строк в таблице     : %d" % len(app.clip_tree.get_children()))
print("  кеш рядом со стримом:", os.path.isfile(asr.cache_path(stream)))

# 2. повтор — расшифровка уже есть
print("\n--- второй прогон (расшифровка готова) ---")
before = calls["n"]
t0 = time.time()
app._scan_sense()
deadline = time.time() + 600
while app.runner.busy() and time.time() < deadline:
    app.update(); time.sleep(0.05)
for _ in range(30):
    app.update(); time.sleep(0.05)
print("  НОВЫХ обращений     : %d, за %.1f с" % (calls["n"] - before, time.time() - t0))

# 3. окно видит расшифровку в шапке
app._refresh_transcript(); app.update()
print("  шапка говорит       :", app.lbl_speech["text"])

# 4. сохранение списка
out = os.path.join(work, "m.txt")
import tkinter.filedialog as fd
fd.asksaveasfilename = lambda **kw: out
app._save_moments()
print("  список записан      :", os.path.isfile(out))
if os.path.isfile(out):
    print("     ", open(out, encoding="utf-8").read().strip().splitlines()[-1][:70])

found = len(getattr(app, "clip_moments", []))
print("  ошибок в окне       : %d %s" % (len(ERRORS), ERRORS[:1]))
good = (first == 1 and (calls["n"] - before) == 0 and not ERRORS
        and len(app.clip_tree.get_children()) == found
        and (found == 0 or os.path.isfile(out)))
app.destroy()
print("\n  ИТОГ:", "отбор по смыслу работает" if good else "ЕСТЬ ПРОБЛЕМА")
sys.exit(0 if good else 1)
