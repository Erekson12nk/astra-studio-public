# -*- coding: utf-8 -*-
"""Обещание шага: звук стрима читается один раз, а не дважды.

Разведка кандидатов сперва считала уровни громкости (полный проход по звуку),
а потом распаковывала весь звук заново ради сорока окон. На пятичасовом стриме
это 163 с из 485. Теперь уровни попутно кладут сырой звук, а `scan_transcript`
его подхватывает. Здесь проверяется, что подложенный звук совпадает с тем,
что дала бы распаковка, и что второго прохода больше нет.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

from media import build as _build

import hashlib
import importlib.util
import os
import shutil
import sys
import tempfile

_MEDIA = _build()
sys.path.insert(0, os.path.join(_REPO, "astra"))

from core import loudness as _loud

_spec = importlib.util.spec_from_file_location(
    "ave_scan", os.path.join(_REPO, "astra", "auto_vertical_editor.py"))
ave = importlib.util.module_from_spec(_spec)
sys.modules["ave_scan"] = ave
_spec.loader.exec_module(ave)

ok = []


def check(label, value):
    ok.append(bool(value))
    print("  %-56s %s" % (label, "ок" if value else "ПРОВАЛ"))


def digest(path):
    return hashlib.sha1(open(path, "rb").read()).hexdigest()


source = _MEDIA["speech"]
work = tempfile.mkdtemp(prefix="scan_audio_")

spare = os.path.join(work, "levels.raw")
side = _loud.audio_levels(source, cache=False, samples=spare)
plain = _loud.audio_levels(source, cache=False)

check("попутный звук записан", os.path.isfile(spare))
check("уровни от попутной записи не поехали",
      side["level"].size == plain["level"].size
      and (side["level"] == plain["level"]).all())

apart = os.path.join(work, "extract.raw")
ave.extract_samples(source, apart)
check("попутный звук байт в байт равен распаковке",
      digest(spare) == digest(apart))

check("готовый звук признан годным", ave.usable_samples(spare))
check("пустого пути нет", not ave.usable_samples(None))
check("отсутствующий файл не берётся",
      not ave.usable_samples(os.path.join(work, "нет.raw")))
crumb = os.path.join(work, "crumb.raw")
open(crumb, "wb").write(b"\0" * 64)
check("обрезок не берётся", not ave.usable_samples(crumb))

calls = []
real_extract = ave.extract_samples
ave.extract_samples = lambda src, dst: (calls.append(dst), real_extract(src, dst))[1]
heard = []
ave.transcribe_samples = lambda model, chunk, language, beam=5: (
    heard.append(len(chunk)),
    ([{"start": 0.0, "end": 1.0, "text": "раз"}], 1.0))[1]

found = [{"start": 0.5, "end": 2.5, "score": 9.0, "peak": 1.0}]
items = ave.scan_transcript(source, found, None, "ru", ready=spare)
check("расшифровка кандидатов прошла", len(items) == 1)
check("звук заново не распаковывался", calls == [])
check("окно взято из готового звука", heard and heard[0] > 0)

items2 = ave.scan_transcript(source, found, None, "ru", ready=None)
check("без готового звука распаковка всё ещё работает", len(items2) == 1)
check("и она случилась ровно один раз", len(calls) == 1)
check("итог тот же, откуда бы звук ни взялся",
      items[0]["text"] == items2[0]["text"]
      and items[0]["start"] == items2[0]["start"])

ave.extract_samples = real_extract
shutil.rmtree(work, ignore_errors=True)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
