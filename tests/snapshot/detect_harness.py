# -*- coding: utf-8 -*-
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

import json, os, sys

ROOT = sys.argv[1]
MEDIA = sys.argv[2]
out = {}
_real = sys.stdout
sys.stdout = sys.stderr


def rec(key, fn):
    try:
        out[key] = {"ok": True, "value": fn()}
    except Exception as e:
        out[key] = {"ok": False, "err": type(e).__name__, "msg": str(e)[:200]}


def r4(x):
    return round(float(x), 4)


# ---------------- astra: скан по громкости ----------------
sys.path.insert(0, os.path.join(ROOT, "astra"))
import auto_vertical_editor as AVE
import numpy as np

data = AVE.audio_levels(MEDIA, cache=False)
rec("levels.shape", lambda: [int(data["level"].size), int(data["voice"].size),
                             data["window"]])
rec("levels.stats", lambda: [r4(np.mean(data["level"])), r4(np.std(data["level"])),
                             r4(np.mean(data["voice"]))])
parts = AVE.moment_scores(data)
rec("scores.stats", lambda: {k: [r4(np.mean(parts[k])), r4(np.std(parts[k]))]
                             for k in sorted(parts)})
rec("pick_moments", lambda: [[r4(m["start"]), r4(m["end"]), r4(m["peak"]),
                              r4(m["score"])]
                             for m in AVE.pick_moments(data, 5, 2.0, 8.0)])
rec("scan_highlights", lambda: [[r4(m["start"]), r4(m["end"])]
                                for m in AVE.scan_highlights(MEDIA, 3, 2.0, 8.0,
                                                             cache=False)])
rec("smooth_series", lambda: [r4(v) for v in
                              AVE.smooth_series(np.arange(10, dtype=np.float32), 3)])
rec("delay_series", lambda: [r4(v) for v in
                             AVE.delay_series(np.arange(6, dtype=np.float32), 2)])
rec("quiet_edge", lambda: int(AVE.quiet_edge(
    data["level"], parts["base"], 10, data["window"], 2.0, True)))
rec("scan_cache_path_stable", lambda: (
    AVE.scan_cache_path(MEDIA, 0.25, 16000) == AVE.scan_cache_path(MEDIA, 0.25, 16000)))
rec("scan_constants", lambda: [AVE.SCAN_WINDOW, AVE.SCAN_RATE, AVE.SCAN_LOW,
                               AVE.SCAN_HIGH, AVE.SCAN_SPAN, AVE.SCAN_GAP,
                               AVE.SCAN_FLOOR, AVE.SCAN_VERSION,
                               sorted(AVE.SCAN_WEIGHTS.items())])

# ---------------- clipper: разбор ответа модели ----------------
sys.path.insert(0, os.path.join(ROOT, "clipper"))
from clipper.backends import base as B

CASES = {
    "чистый": '{"clips":[{"start_sentence":1,"end_sentence":5,"title":"т",'
              '"hook":"х","reason":"р","score":8}]}',
    "в markdown": '```json\n{"clips":[{"start_sentence":2,"end_sentence":4,'
                  '"title":"","hook":"","reason":"","score":7.5}]}\n```',
    "с болтовнёй": 'Вот что я выбрал:\n{"clips":[{"start_sentence":3,'
                   '"end_sentence":9,"title":"a","hook":"b","reason":"c",'
                   '"score":6}]}\nНадеюсь, подойдёт.',
    "висячая запятая": '{"clips":[{"start_sentence":1,"end_sentence":2,'
                       '"title":"x","hook":"y","reason":"z","score":5,},]}',
    "think": '<think>Прикину форму: {"clips": [...]}. Ага.</think>'
             '{"clips":[{"start_sentence":7,"end_sentence":8,"title":"t",'
             '"hook":"h","reason":"r","score":9}]}',
    "think без закрытия": '<think>рассуждаю и не закрываю {',
    "голый список": '[{"start_sentence":1,"end_sentence":3,"title":"","hook":"",'
                    '"reason":"","score":4}]',
    "другой ключ": '{"fragments":[{"start_sentence":5,"end_sentence":6,'
                   '"title":"","hook":"","reason":"","score":3}]}',
    "мусор": 'извините, ничего не нашёл',
    "пусто": '',
    "без полей": '{"clips":[{"title":"нет границ"}]}',
    "нечисло": '{"clips":[{"start_sentence":"a","end_sentence":2}]}',
}
for name, text in CASES.items():
    rec("json." + name, lambda t=text: B.coerce_clips(B.extract_json(t)))

_real.write(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True, default=str))
