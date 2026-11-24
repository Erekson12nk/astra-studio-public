# -*- coding: utf-8 -*-
"""Модель-шпион: записывает параметры вызова и отдаёт заготовленные сегменты.

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

Нужен потому, что расшифровка синтезированной речи не детерминирована
(astra не передаёт condition_on_previous_text=False, и текст пляшет от
запуска к запуску). Сравнивать текст бессмысленно, а параметры вызова
и постобработку — можно, и строго.

Работает одинаково на старом и новом дереве: точки подмены выбраны так,
чтобы существовать в обоих.
"""
import json, os, sys, tempfile

ROOT = sys.argv[1]
out = {}


class W:
    def __init__(self, start, end, word, probability=1.0):
        self.start, self.end, self.word, self.probability = start, end, word, probability


class S:
    def __init__(self, start, end, text, words, no_speech_prob=0.0):
        self.start, self.end, self.text = start, end, text
        self.words, self.no_speech_prob = words, no_speech_prob


class Info:
    language = "en"
    language_probability = 0.9
    duration = 12.0


def segments():
    # намеренно каверзные данные: пробелы по краям, пустое слово,
    # нулевая длительность, слова не по порядку, None в таймкоде
    return [
        S(0.0, 2.0, "  Hello world  ", [
            W(0.0, 0.5, " Hello ", 0.8),
            W(0.5, 0.5, " world ", 0.6),        # end == start
            W(0.9, 1.2, "   ", 0.5),            # пустое после strip
        ], no_speech_prob=0.10),
        S(2.0, 4.0, "second", [
            W(3.0, 3.5, " later ", 0.7),
            W(2.1, 2.4, " earlier ", 0.9),      # не по порядку
            W(None, 2.9, " broken ", 0.4),      # нет начала
        ], no_speech_prob=0.40),
    ]


class SpyModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append({k: (v if isinstance(v, (str, int, float, bool,
                                                   type(None), dict)) else str(v))
                           for k, v in kw.items()})
        return iter(segments()), Info()


def rec(key, fn):
    try:
        out[key] = {"ok": True, "value": fn()}
    except Exception as e:
        out[key] = {"ok": False, "err": type(e).__name__, "msg": str(e)[:200]}


# ---------------------------------------------------------------- astra
sys.path.insert(0, os.path.join(ROOT, "astra"))
import auto_vertical_editor as AVE

spy = SpyModel()
rec("astra.transcribe.words", lambda: AVE.transcribe(spy, "audio.wav", "en"))
rec("astra.transcribe.kwargs", lambda: spy.calls[-1])

rec("astra.samples.words", lambda: AVE.transcribe_samples(spy, "samples", "en")[0])
rec("astra.samples.speech",
    lambda: round(AVE.transcribe_samples(spy, "samples", "en")[1], 4))
rec("astra.samples.kwargs", lambda: spy.calls[-1])

# ---------------------------------------------------------------- audioclean
sys.path.insert(0, os.path.join(ROOT, "audioclean"))
from audioclean.config import Settings
from audioclean import transcribe as ATR

spy2 = SpyModel()
cfg = Settings(model="tiny", language="en", device="cpu", beam_size=3, vad=True)
rec("audioclean.segments", lambda: ATR.segments_to_dict(
    ATR._run(spy2, "a.wav", cfg, 0.0, None, None, None)))
rec("audioclean.kwargs", lambda: spy2.calls[-1])

cfg2 = Settings(model="tiny", language="", device="cpu", beam_size=5, vad=False)
ATR._run(spy2, "a.wav", cfg2, 0.0, None, None, None)
rec("audioclean.kwargs_novad", lambda: spy2.calls[-1])

rec("audioclean.split_segment", lambda: [
    [round(w.start, 3), round(w.end, 3), w.text, w.prob]
    for w in ATR.split_segment(ATR.Segment(1.0, 3.0, "one two three", []))])
rec("audioclean.to_srt", lambda: ATR.to_srt(
    [ATR.Segment(1.5, 3.25, "hello"), ATR.Segment(4.0, 5.0, "world")]))

# ---------------------------------------------------------------- clipper
sys.path.insert(0, os.path.join(ROOT, "clipper"))
from pathlib import Path
from clipper.config import Config
from clipper import transcribe as CTR

spy3 = SpyModel()
work = Path(tempfile.mkdtemp())
ccfg = Config(work_dir=work, out_dir=work / "out")
ccfg.whisper_model = "tiny"
ccfg.language = "ru"

CTR._load_model = lambda cfg: spy3
CTR.extract_audio = lambda src, dst, *a, **k: dst

source = work / "movie.mp4"
source.write_bytes(b"x" * 100)


def _clipper():
    t = CTR.transcribe(source, ccfg, force=True)
    return {"language": t.language, "duration": t.duration,
            "sentences": [{"idx": s.idx, "start": s.start, "end": s.end,
                           "text": s.text,
                           "words": [[w.start, w.end, w.text] for w in s.words]}
                          for s in t.sentences]}


rec("clipper.transcript", _clipper)
rec("clipper.kwargs", lambda: spy3.calls[-1])
rec("clipper.cached_again", _clipper)

print(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True, default=str))
