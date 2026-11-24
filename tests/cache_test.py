# -*- coding: utf-8 -*-
"""Проверяет главное обещание шага: расшифровка одного файла не повторяется.



audioclean расшифровывает файл, потом clipper берёт тот же файл — и должен
взять готовое, ни разу не тронув whisper. Считаем реальные обращения
к модели, а не верим на слово.
"""

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

import json, os, shutil, sys, tempfile, time
from pathlib import Path

ROOT = _REPO
SRC = sys.argv[1] if len(sys.argv) > 1 else _MEDIA["speech"]

work = Path(tempfile.mkdtemp())
video = work / "stream.mp4"
shutil.copy2(SRC, video)

calls = {"n": 0}

sys.path.insert(0, ROOT)

from core import asr as CORE

_real = CORE.transcribe


def counted(*a, **kw):
    calls["n"] += 1
    return _real(*a, **kw)


CORE.transcribe = counted

from audioclean.config import Settings
from audioclean import pipeline as AP

from clipper.config import Config
from clipper import transcribe as CTR

report = {}

# --- 1. audioclean расшифровывает файл ------------------------------------
s = Settings(video=str(video), model="tiny", language="en",
             device="cpu", beam_size=5, vad=True)
t0 = time.time()
res = AP.analyze(s)
report["1_audioclean"] = {"обращений к whisper": calls["n"],
                          "секунд": round(time.time() - t0, 1),
                          "сегментов": len(res.segments)}

# --- 2. clipper берёт тот же файл -----------------------------------------
cfg = Config(work_dir=work / "clipper", out_dir=work / "clipper" / "out")
cfg.whisper_model = "tiny"
cfg.whisper_device = "cpu"
cfg.whisper_compute = "int8"
cfg.language = "en"

before = calls["n"]
t0 = time.time()
tr = CTR.transcribe(video, cfg)
report["2_clipper"] = {"новых обращений к whisper": calls["n"] - before,
                       "секунд": round(time.time() - t0, 1),
                       "предложений": len(tr.sentences)}

# --- 3. audioclean ещё раз -------------------------------------------------
before = calls["n"]
AP.analyze(s)
report["3_audioclean_снова"] = {"новых обращений к whisper": calls["n"] - before}

# --- 4. умолчания сходятся: один и тот же ключ у всех трёх программ -------
sys.path.insert(0, os.path.join(ROOT, "astra"))
import auto_vertical_editor as AVE

same = Settings(video=str(video))
clean_key = AP._key(same)
cut = Config(work_dir=work / "c2", out_dir=work / "c2" / "out")
clip_key = CTR._key(cut, video)
astra_key = CORE.cache_key(video, AVE.DEFAULT_WHISPER, same.language,
                           AVE.SCAN_BEAM, True)
report["4_умолчания"] = {
    "audioclean": clean_key, "clipper": clip_key, "astra": astra_key,
    "ключи совпадают": clean_key == clip_key == astra_key,
}

report["файл_кеша"] = os.path.basename(str(CORE.cache_path(video)))
report["кеш_существует"] = CORE.cache_path(video).exists()
report["ИТОГО обращений"] = calls["n"]

print(json.dumps(report, ensure_ascii=False, indent=1))

if not report["4_умолчания"]["ключи совпадают"]:
    print("ПРОВАЛ: программы по умолчанию не находят чужую расшифровку")
    sys.exit(1)
