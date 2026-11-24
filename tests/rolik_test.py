# -*- coding: utf-8 -*-
"""Проверяет режим «ролики»: стрим -> clipmaker -> audioclean без повторного whisper."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

from media import build as _build, CACHE as _CACHE

_MEDIA = _build()
_CACHE_U = _CACHE.replace("\\", "/")

import os, shutil, sys, tempfile, time

ROOT = _REPO
SC = _CACHE

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "clipmaker"))

from core import asr, handoff

calls = {"n": 0}
_real = asr.transcribe


def counted(*a, **kw):
    calls["n"] += 1
    return _real(*a, **kw)


asr.transcribe = counted

import clipmaker
from audioclean.config import Settings
from audioclean import pipeline as AP

work = tempfile.mkdtemp()
stream = os.path.join(work, "stream.mp4")
shutil.copy2(os.path.join(SC, "speech.mp4"), stream)

# 1. по стриму уже прошёлся audioclean (как в жизни: сначала чистим весь стрим)
s = Settings(video=stream, model="tiny", language="en", device="cpu",
             beam_size=5, vad=True)
t0 = time.time()
whole = AP.analyze(s)
print("1. стрим расшифрован : %d обращений к whisper, %.1f с, слов %d"
      % (calls["n"], time.time() - t0, len(whole.words)))

# 2. режем ролик через clipmaker
rolik = os.path.join(work, "rolik.mp4")
code = clipmaker.make_clip({"input": stream, "start": "4", "end": "+8",
                            "output": rolik, "crf": 30, "preset": "ultrafast"},
                           on_line=lambda m: None)
print("2. ролик собран      : код %d, отметка рядом: %s"
      % (code, os.path.isfile(handoff.origin_path(rolik))))
origin = handoff.read_origin(rolik)
print("   отметка говорит   : %s  %.1f..%.1f"
      % (os.path.basename(origin["source"]), origin["start"], origin["end"]))

# 3. чистим мат в ролике
before = calls["n"]
s2 = Settings(video=rolik, model="tiny", language="en", device="cpu",
              beam_size=5, vad=True)
t0 = time.time()
logs = []
res = AP.analyze(s2, log=logs.append)
print("3. ролик обработан   : %d НОВЫХ обращений к whisper, %.2f с, слов %d"
      % (calls["n"] - before, time.time() - t0, len(res.words)))
for line in logs:
    if "расшифровку исходного" in line or "кэша" in line:
        print("   лог               :", line)

inside = [w for w in whole.words if w.start >= 4.0 and w.end <= 12.0]
print("   слов в стриме на 4..12 с: %d, в ролике: %d" % (len(inside), len(res.words)))
if res.words:
    print("   первое слово ролика : %.2f с  %r (в стриме было бы %.2f)"
          % (res.words[0].start, res.words[0].text, res.words[0].start + 4.0))

# 4. без отметки — обычный путь
plain = os.path.join(work, "plain.mp4")
shutil.copy2(rolik, plain)
before = calls["n"]
AP.analyze(Settings(video=plain, model="tiny", language="en", device="cpu",
                    beam_size=5, vad=True))
print("4. копия без отметки : %d новых обращений (ожидается 1)" % (calls["n"] - before))

print("\nВСЕГО обращений к whisper: %d" % calls["n"])
