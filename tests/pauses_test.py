# -*- coding: utf-8 -*-
"""Вырезание пауз не должно съедать сам клип.

Нашлось на настоящем куске стрима: 8 секунд тихой заставки движок счёл
сплошной паузой и оставил от клипа 0.3 секунды. Виноват был отступ вокруг
речи — у краёв клипа защищать нечего, а он всё равно резал по живому.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import importlib.util

spec = importlib.util.spec_from_file_location(
    "ave_pauses", _os.path.join(_REPO, "astra", "auto_vertical_editor.py"))
ave = importlib.util.module_from_spec(spec)
_sys.modules["ave_pauses"] = ave
spec.loader.exec_module(ave)

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-54s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


PAD = 0.12
LONG = 15.0

print("\n--- что остаётся после вырезания пауз ---")

whole = ave.keep_segments([(0.0, LONG)], LONG, PAD)
check("клип целиком тихий — не оставляем огрызков", whole == [], whole)

middle = ave.keep_segments([(4.0, 6.0)], LONG, PAD)
check("пауза посередине — два куска", len(middle) == 2, middle)
check("отступ оставил воздух вокруг речи",
      middle[0][1] > 4.0 and middle[1][0] < 6.0, middle)

head = ave.keep_segments([(0.0, 3.0)], LONG, PAD)
check("тишина в начале — один кусок", len(head) == 1, head)
check("и он не начинается внутри тишины", head[0][0] >= 3.0 - PAD - 0.01, head)

tail = ave.keep_segments([(12.0, LONG)], LONG, PAD)
check("тишина в конце — один кусок", len(tail) == 1, tail)
check("и он не кончается внутри тишины", tail[0][1] <= 12.0 + PAD + 0.01, tail)

both = ave.keep_segments([(0.0, 2.0), (13.0, LONG)], LONG, PAD)
check("тишина с обоих концов — середина цела", len(both) == 1, both)

many = ave.keep_segments([(2.0, 3.0), (6.0, 7.0), (10.0, 11.0)], LONG, PAD)
check("три паузы — четыре куска", len(many) == 4, many)
check("куски идут по возрастанию и не налезают",
      all(a[1] <= b[0] for a, b in zip(many, many[1:])), many)
check("ничего не вылезло за длину клипа",
      all(0.0 <= a and b <= LONG for a, b in many))

print("\n--- порог здравого смысла ---")


class Args:
    pause_threshold = -32
    pause_min = 0.45
    pause_keep = PAD
    pause_max_cuts = 150
    crf = 20
    preset = "veryfast"


spans = {"value": [(0.0, LONG)]}
cut = {"called": False}
ave.detect_silence = lambda *a, **k: spans["value"]
ave.remove_pauses = lambda *a, **k: cut.update(called=True)

said = []
_print = print


def quiet(*args, **kwargs):
    said.append(" ".join(str(a) for a in args))


import builtins

builtins.print = quiet
left = ave.tighten_clip("нет.mp4", "нет2.mp4", LONG, Args(), "libx264")
builtins.print = _print
check("сплошная тишина — клип не трогаем", left is None and not cut["called"])
check("и сказано, почему", any("silent" in line or "quiet" in line for line in said), said)

spans["value"] = [(0.3, 14.85)]
said.clear()
cut["called"] = False
builtins.print = quiet
left = ave.tighten_clip("нет.mp4", "нет2.mp4", LONG, Args(), "libx264")
builtins.print = _print
check("осталась бы доля секунды — тоже не трогаем",
      left is None and not cut["called"], left)

spans["value"] = [(1.0, 13.0)]
said.clear()
cut["called"] = False
builtins.print = quiet
left = ave.tighten_clip("нет.mp4", "нет2.mp4", LONG, Args(), "libx264")
builtins.print = _print
check("а большую настоящую паузу режем, даже если клип станет вчетверо короче",
      cut["called"] and left and 2.0 < left < 4.0, left)

spans["value"] = [(5.0, 7.0)]
said.clear()
cut["called"] = False
builtins.print = quiet
left = ave.tighten_clip("нет.mp4", "нет2.mp4", LONG, Args(), "libx264")
builtins.print = _print
check("настоящую паузу по-прежнему режем", cut["called"] and left and left > 12.0, left)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
raise SystemExit(1 if ok.count(False) else 0)
