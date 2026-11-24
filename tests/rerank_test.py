# -*- coding: utf-8 -*-
"""Реранкер: обучение на сравнениях и оценках, честная базовая линия."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os, random, sys, tempfile

from core import rerank as R

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


def candidate(cid, score, duration, chars):
    return {"id": cid, "model_score": score,
            "features": {"duration": duration, "n_sentences": 4, "chars": chars,
                         "position_ratio": 0.5, "words_per_sec": 2.5}}


# --- оценки превращаются в пары ---
pairs = R.pairs_from_ratings({1: 5, 2: 3, 3: 5, 4: 0})
check("из оценок получаются пары", (1, 2) in pairs and (3, 2) in pairs, len(pairs))
check("равные оценки пару не дают", (1, 3) not in pairs and (3, 1) not in pairs)
check("отклонённые (0) не участвуют",
      all(4 not in pair for pair in pairs))

# --- мало данных: не обучаем и говорим почему ---
rows = [candidate(i, 9.0, 30.0, 300) for i in range(1, 6)]
model, report = R.train(rows, {1: 5, 2: 4}, [])
check("на трёх парах обучение отказывается", model is None)
check("отказ объясняется словами", "мало" in report.get("reason", ""),
      report.get("reason"))

# --- насыщенный движок: равные оценки считаются как монетка ---
raw = {1: [9.0], 2: [9.0], 3: [9.0]}
check("равные оценки движка дают базовую линию 0.5",
      R.baseline_accuracy([(1, 2), (2, 3)], raw) == 0.5)
check("движок, который угадал, даёт 1.0",
      R.baseline_accuracy([(1, 2)], {1: [9.0], 2: [5.0]}) == 1.0)
check("движок, который промахнулся, даёт 0.0",
      R.baseline_accuracy([(1, 2)], {1: [5.0], 2: [9.0]}) == 0.0)

# --- выучиваемая закономерность: короткое лучше длинного ---
random.seed(3)
rows = []
for cid in range(1, 41):
    length = 15.0 + cid * 2.0
    rows.append(candidate(cid, 9.0, length, int(length * 12)))
comparisons = []
for _ in range(120):
    a, b = random.sample(range(1, 41), 2)
    short, long_ = (a, b) if a < b else (b, a)
    comparisons.append((short, long_))
model, report = R.train(rows, {}, comparisons)
check("на осмысленных данных модель обучается", model is not None)
check("модель обыгрывает насыщенный движок", report["better_than_engine"],
      "%.2f против %.2f" % (report["holdout_accuracy"], report["baseline_accuracy"]))
check("на отложенных выше половины", report["holdout_accuracy"] > 0.5,
      "%.2f" % report["holdout_accuracy"])
check("короткое получает балл выше длинного",
      R.score(model, rows[0]) > R.score(model, rows[-1]))
check("маленькая отложенная выборка помечается", "shaky" in report)

# --- сохранение и чтение ---
work = tempfile.mkdtemp()
path = R.save(model, os.path.join(work, R.FILE_NAME))
again = R.load(path)
check("модель читается обратно", again is not None)
check("баллы после чтения те же",
      abs(R.score(again, rows[0]) - R.score(model, rows[0])) < 1e-9)
check("нет файла — нет модели", R.load(os.path.join(work, "нет.json")) is None)
open(os.path.join(work, "мусор.json"), "w").write("{не json")
check("битый файл не роняет", R.load(os.path.join(work, "мусор.json")) is None)

# --- признаки, которых нет, не ломают ---
check("кандидат без признаков считается",
      isinstance(R.score(model, {"id": 1, "model_score": 5.0}), float))

# --- модель помнит, чем закончилось обучение ---
check("итог обучения сохранён в самой модели", "report" in model)
check("в нём есть сравнение с движком",
      model["report"].get("better_than_engine") is not None)
check("и число пар", model["report"].get("pairs") == report["pairs"])
check("проставлено время обучения", model.get("trained_at", 0) > 0)
check("итог переживает запись и чтение",
      again["report"]["pairs"] == model["report"]["pairs"])

# --- решение включать ли модель саму ---
import argparse

from clipper import cli as CLI


from clipper.config import Config


def Cfg(folder):
    return Config(work_dir=__import__("pathlib").Path(folder))


def flags(**extra):
    values = {"rerank": False, "no_rerank": False}
    values.update(extra)
    return argparse.Namespace(**values)


cfg = Cfg(work)
check("обученная модель подхватывается сама",
      CLI.use_rerank(flags(), cfg) is not None)
check("--no-rerank выключает", CLI.use_rerank(flags(no_rerank=True), cfg) is None)

пусто = Cfg(tempfile.mkdtemp())
check("нет модели — молча порядок движка", CLI.use_rerank(flags(), пусто) is None)
check("нет модели, но просили — тоже None",
      CLI.use_rerank(flags(rerank=True), пусто) is None)

слабая = dict(model)
слабая["report"] = dict(model["report"])
слабая["report"]["better_than_engine"] = False
слабая["report"]["holdout_accuracy"] = 0.48
слабая["report"]["baseline_accuracy"] = 0.50
плохо = Cfg(tempfile.mkdtemp())
R.save(слабая, os.path.join(str(плохо.work_dir), R.FILE_NAME))
check("модель хуже движка сама не включается",
      CLI.use_rerank(flags(), плохо) is None)
check("но --rerank настаивает",
      CLI.use_rerank(flags(rerank=True), плохо) is not None)

старая = {k: v for k, v in model.items() if k != "report"}
древность = Cfg(tempfile.mkdtemp())
R.save(старая, os.path.join(str(древность.work_dir), R.FILE_NAME))
check("модель без итога обучения включается как раньше",
      CLI.use_rerank(flags(), древность) is not None)


print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
