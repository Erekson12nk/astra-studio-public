# -*- coding: utf-8 -*-
"""Запуск: прогноз тиктоков и роликов, учёба на просмотрах каналов, один экземпляр.

Каналы, движок и сеть подставные.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _REPO)

import shutil
import tempfile
import time

work = tempfile.mkdtemp(prefix="launch_test_")
_os.environ["ASTRA_PROFILES"] = _os.path.join(work, "profiles")
_os.environ["ASTRA_STUDIO_SETTINGS"] = _os.path.join(work, "settings.json")

from core import launch
from core import profiles as _pro

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


def moment(start, length=40, score=7.0):
    return {"start": float(start), "end": float(start + length), "score": score}


print("прогноз по найденным моментам")
empty = launch.estimate([])
check("ничего не нашлось — ноль и ноль", empty["tiktoks"] == 0 and empty["roliks"] == 0)
scattered = [moment(t) for t in (0, 3000, 6000, 9000)]
found = launch.estimate(scattered)
check("редкие моменты — тиктоки, но не ролик", found == dict(found, tiktoks=4, roliks=0), found)
dense = [moment(1000 + 150 * i) for i in range(5)]          # 10 минут подряд
found = launch.estimate(dense + scattered)
check("плотный кусок стрима — один ролик", found["roliks"] == 1 and found["tiktoks"] == 9,
      found)
long_run = [moment(20000 + 200 * i) for i in range(16)]     # почти 54 минуты подряд
check("длинный плотный кусок — несколько роликов",
      launch.estimate(long_run)["roliks"] == 2, launch.estimate(long_run))
weak = [moment(1000 + 150 * i, score=3.0) for i in range(5)]
check("слабые моменты не считаются", launch.estimate(weak, least_score=5.0)["tiktoks"] == 0)
twice = [moment(100), moment(110), moment(500)]
check("перекрытые моменты — один тикток", launch.estimate(twice)["tiktoks"] == 2)
check("слова склоняются", launch.estimate_text({"tiktoks": 21, "roliks": 3})
      == "Из этого стрима примерно: 21 тикток и 3 больших ролика"
      and launch.plural(12, "тикток", "тиктока", "тиктоков") == "12 тиктоков"
      and launch.plural(5, "большой ролик", "больших ролика", "больших роликов")
      == "5 больших роликов")

print("когда учиться")
NOW = 1792600000.0
DAY = 86400.0
check("ни разу не учились — пора", launch.learn_due({}, now=NOW))
check("учились час назад — рано", not launch.learn_due({"learned_at": NOW - 3600}, now=NOW))
check("учились вчера — пора", launch.learn_due({"learned_at": NOW - DAY - 1}, now=NOW))
check("учёба выключена — не учимся", not launch.learn_due({"learn_auto": False}, now=NOW))
check("по умолчанию — свои выгрузки", launch.learn_sources({}) == ("own",))
check("мусор в настройке — свои выгрузки", launch.learn_sources({"learn_from": ["x"]})
      == ("own",))

print("учёба целиком")
_pro.save({"name": "стример", "created": time.time(), "face": "", "keep_days": 3})
_pro.set_active("стример")
profile = _pro.load("стример")
db = _os.path.join(work, "нет.db")
built = []


def builder(profile_, db_, backend, cfg, log=None, sources=(), now=None):
    built.append(sources)
    return {"rules": ["брать споры с чатом"], "based_on": 12}


report = launch.learn(profile, {}, db, engine=("движок", "cfg"), builder=builder)
check("правила собраны по своим выгрузкам", built == [("own",)]
      and report["rules"]["rules"] == ["брать споры с чатом"])
report = launch.learn(profile, {}, db, engine=None)
check("без движка — правила ждут", report["rules"] is None and "движка" in report["skipped"])


def unlearned(*a, **k):
    raise RuntimeError("мало данных")


report = launch.learn(profile, {}, db, engine=("движок", "cfg"), builder=unlearned)
check("мало данных — сказано, правила не тронуты", report["skipped"] == "мало данных")

print("один экземпляр")
first = launch.Single(port=47699)
woke = []
first.on_show = lambda: woke.append(1)
check("первая Студия занимает место", first.claim())
second = launch.Single(port=47699)
check("вторая — нет", not second.claim())
check("вторая будит первую", second.wake_other())
deadline = time.time() + 3
while not woke and time.time() < deadline:
    time.sleep(0.05)
check("первая проснулась", woke == [1])
first.release()
third = launch.Single(port=47699)
check("первая закрылась — место свободно", third.claim())
third.release()

shutil.rmtree(work, ignore_errors=True)
print("\nитог: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
