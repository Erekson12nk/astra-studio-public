# -*- coding: utf-8 -*-
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import contextlib, io, os, sys, tempfile

from core import bench as B

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


def near(a, b):
    return abs(a - b) < 1e-6


work = tempfile.mkdtemp(prefix="bench_test_")
marks_path = os.path.join(work, "marks.txt")
with open(marks_path, "w", encoding="utf-8") as handle:
    handle.write("# Моменты для монтажа. Формат: начало - конец  заголовок\n"
                 "# оценка 9.0\n"
                 "0:01:40.000 - 0:02:00.000  первый\n"
                 "0:05:00 - 0:05:30  второй\n"
                 "0:10:00.000 - 0:10:10.000  третий\n"
                 "0:20:00.000 - 0:20:40.000  четвёртый\n"
                 "мусорная строка\n")

marks = B.load_marks(marks_path)
check("разметка читается в формате handoff", len(marks) == 4, len(marks))
check("время разобрано", near(marks[1]["start"], 300.0) and near(marks[1]["end"], 330.0))

check("пересечение считается в секундах", near(B.overlap((0, 10), (7, 20)), 3.0))
check("непересекающиеся дают ноль", B.overlap((0, 10), (11, 20)) == 0.0)

cands = [
    {"start": 97.0, "end": 103.0},
    {"start": 328.0, "end": 360.0},
    {"start": 605.0, "end": 640.0},
    {"start": 1000.0, "end": 1030.0},
    {"start": 1990.0, "end": 2003.0},
]
row = B.score(marks, cands)
check("покрытие: 2 из 4 (порог ровно 3 с проходит)", near(row["coverage"], 0.5), row)
check("второй с перехлёстом 2 с не засчитан", row["found"] == 2)
check("точность: 2 из 5 кандидатов", near(row["precision"], 0.4))
check("хронометраж выдачи — сумма длин", near(row["total"], 6 + 32 + 35 + 30 + 13))

both = B.score(marks, [{"start": 100, "end": 1220}])
check("один длинный кандидат покрывает всё", both["found"] == 4 and both["right"] == 1)
two = B.score(marks[:1], [(100, 110), (105, 115)])
check("два кандидата на одну метку оба точные", two["right"] == 2 and two["found"] == 1)
check("пустая выдача не падает", B.score(marks, [])["precision"] == 0.0)
check("пустая разметка не падает", B.score([], cands)["coverage"] == 0.0)

flat = B.uniform(3600.0, 4, 60.0)
check("равномерно: столько клипов, сколько просили", len(flat) == 4)
check("равномерно: центры через равный шаг",
      [c["start"] for c in flat] == [420.0, 1320.0, 2220.0, 3120.0], flat)
check("равномерно: длина клипа", all(near(c["end"] - c["start"], 60.0) for c in flat))
check("равномерно: длина не больше шага", near(B.uniform(100, 4, 60)[0]["end"]
                                              - B.uniform(100, 4, 60)[0]["start"], 25.0))

even = B.equalize([(10, 20), (100, 130)], 80.0, duration=1000.0)
check("уравнивание: сумма длин равна заданной",
      near(sum(c["end"] - c["start"] for c in even), 80.0), even)
check("уравнивание: растягивает вокруг середины",
      near(even[1]["start"], 85.0) and near(even[1]["end"], 145.0))
edge = B.equalize([(0, 10), (990, 1000)], 40.0, duration=1000.0)
check("уравнивание: у краёв сдвигает внутрь, длина та же",
      near(edge[0]["start"], 0.0) and near(edge[0]["end"], 20.0)
      and near(edge[1]["end"], 1000.0) and near(edge[1]["start"], 980.0), edge)

searches = [("звук", lambda count: cands[:count]), ("смысл", list(reversed(cands)))]
rows = dict(B.compare(marks, searches, 3, 3600.0))
check("сравнение: есть базовая линия", B.UNIFORM in rows)
check("сравнение: выдача обрезана до count",
      rows["звук"]["candidates"] == 3 and rows["смысл"]["candidates"] == 3)
total = 6 + 32 + 35
check("сравнение: хронометраж у всех как у первого",
      all(near(r["total"], total) for r in rows.values()),
      {k: round(v["total"], 3) for k, v in rows.items()})
fixed = dict(B.compare(marks, searches, 3, 3600.0, length=50.0))
check("сравнение: --length задаёт хронометраж",
      all(near(r["total"], 150.0) for r in fixed.values()))

text = B.table([(3, list(rows.items()))])
check("таблица по-русски с покрытием и точностью",
      "3 клипов, покрытие" in text and "точность" in text and "равномерно" in text, "\n" + text)

log = os.path.join(work, "profile", "bench.log")
B.write_log(log, "stream.mp4", marks_path, [(3, list(rows.items()))], rev="abc1234")
B.write_log(log, "stream.mp4", marks_path, [(3, list(rows.items()))], rev="abc1234")
with open(log, encoding="utf-8") as handle:
    lines = handle.read().splitlines()
check("журнал дописывается, а не перезаписывается", len(lines) == 6, len(lines))
check("в журнале видео, разметка, версия и цифры",
      "stream.mp4" in lines[0] and "marks.txt" in lines[0] and "abc1234" in lines[0]
      and "покрытие" in lines[0])

os.environ["ASTRA_PROFILES"] = os.path.join(work, "profiles")
B.sound_search = lambda video, *a, **k: (lambda count: cands[:count], 3600.0)
B.has_sound_cache = lambda video: True
other = os.path.join(work, "other.txt")
with open(other, "w", encoding="utf-8") as handle:
    handle.write("0:01:45 - 0:02:20  с\n0:30:00 - 0:30:40  мимо\n")
out = io.StringIO()
with contextlib.redirect_stdout(out):
    code = B.main(["stream.mp4", marks_path, "--count", "2", "3",
                   "--from", "clipper=" + other])
said = out.getvalue()
check("команда отрабатывает", code == 0, said if code else "")
check("команда печатает оба размера выдачи",
      "2 клипов" in said and "3 клипов" in said and "clipper" in said)
check("команда пишет bench.log рядом с профилем",
      os.path.isfile(os.path.join(work, "profiles", "bench.log")))

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
