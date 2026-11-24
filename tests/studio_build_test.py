# -*- coding: utf-8 -*-
"""Сквозная проверка режима «Ролики» через само окно: сборка + цензура."""
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

from core import asr, handoff, ffmpeg as FF

calls = {"n": 0}
_real = asr.transcribe


def counted(*a, **kw):
    calls["n"] += 1
    return _real(*a, **kw)


asr.transcribe = counted

from studio.window import Studio

work = tempfile.mkdtemp()
stream = os.path.join(work, "stream.mp4")
shutil.copy2(os.path.join(SC, "speech.mp4"), stream)
outdir = os.path.join(work, "out")
os.makedirs(outdir)

app = Studio()
app.source.set(stream)
app._refresh_source()
app.update()

# расшифровка стрима один раз (как сделал бы пользователь кнопкой)
app.model.set("tiny")
app.language.set("en")
key = app._transcript_key()
model = asr.load_model("tiny", "cpu", "int8")
wav = os.path.join(work, "a.wav")
FF.extract_audio(stream, wav, 16000)
asr.save_cache(stream, key, asr.transcribe(wav, model, language="en", vad=True,
                                           vad_min_silence_ms=400,
                                           condition_on_previous_text=False))
print("  стрим расшифрован, обращений к whisper: %d" % calls["n"])
app._refresh_transcript()
app.update()
print("  окно видит расшифровку:", "готова" in app.lbl_speech["text"])

# два ролика
app.moments = [{"start": 2.0, "end": 7.0, "title": "Первый"},
               {"start": 8.0, "end": 13.0, "title": "Второй"}]
app._redraw_moments()
app.v_outdir.set(outdir)
app.v_outro.set("")
app.v_music.set("")
app.v_crf.set("32")
app.v_censor.set(True)
app.update()

before = calls["n"]
t0 = time.time()
app._build_roliks()
deadline = time.time() + 240
while app.runner.busy() and time.time() < deadline:
    app.update()
    time.sleep(0.05)
for _ in range(30):
    app.update()
    time.sleep(0.05)
took = time.time() - t0

rough = os.path.join(outdir, "с матом")
clean_dir = os.path.join(outdir, "без мата")
files = sorted(f for f in os.listdir(rough) if f.endswith(".mp4"))
cleaned = sorted(f for f in os.listdir(clean_dir) if f.endswith(".mp4"))
print("\n  собрано файлов: %d за %.1f с, без мата: %d" % (len(files), took, len(cleaned)))
for f in files:
    p = os.path.join(rough, f)
    origin = handoff.read_origin(p)
    mark = ("отметка %.1f..%.1f" % (origin["start"], origin["end"])) if origin else "без отметки"
    print("    %-28s %6.2f с  %s" % (f, FF.duration(p), mark))

print("\n  НОВЫХ обращений к whisper при сборке и цензуре: %d" % (calls["n"] - before))
tail = app.log.get("1.0", "end").strip().splitlines()
for line in tail:
    if "исходного стрима" in line or "Найдено запрещ" in line or "нечего глушить" in line:
        print("    лог:", line.strip())

app.destroy()
good = (len(files) >= 2 and cleaned == files and not any(
    f.endswith(".mp4") for f in os.listdir(outdir)) and (calls["n"] - before) == 0)
print("\n  ИТОГ:", "режим «Ролики» работает" if good else "ЕСТЬ ПРОБЛЕМА")
sys.exit(0 if good else 1)
