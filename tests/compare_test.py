# -*- coding: utf-8 -*-
"""Сравнение фрагментов между собой: порядок, пары в базе, откат на оценки."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import sys
import tempfile
from pathlib import Path

from clipper import compare as C
from clipper.backends.base import coerce_order, render_items
from clipper.config import Config
from clipper.models import Candidate, Sentence, Transcript
from clipper.store import MODEL, Store

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-56s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


def make(count):
    sents, cands = [], []
    for n in range(count):
        sents.append(Sentence(idx=n, start=n * 10.0, end=n * 10.0 + 9.0,
                              text="реплика номер %d" % n))
        cands.append(Candidate(start=n * 10.0, end=n * 10.0 + 9.0,
                               title="фрагмент %d" % n, reason="", score=9.0,
                               hook="", start_sentence=n, end_sentence=n))
    return Transcript(sentences=sents, duration=count * 10.0), cands


class Judge:
    """Судит по спрятанной силе: чем меньше номер, тем сильнее фрагмент."""

    name = "судья"

    def __init__(self, truth, fail_every=0):
        self.truth = truth
        self.fail_every = fail_every
        self.calls = 0

    def compare_group(self, items, cfg):
        self.calls += 1
        if self.fail_every and self.calls % self.fail_every == 0:
            raise RuntimeError("движок отвалился")
        order = sorted(range(len(items)),
                       key=lambda k: self.truth[items[k]["title"]])
        return order


print("\n--- разбор ответа модели ---")
check("номера с единицы становятся местами с нуля",
      coerce_order({"order": [3, 1, 2]}, 3) == [2, 0, 1])
check("неназванные фрагменты не дописываются — их просто не сравнивали",
      coerce_order({"order": [2]}, 3) == [1])
check("повторы и мусор не ломают порядок",
      coerce_order({"order": [2, 2, "х", 99, 1]}, 3) == [1, 0])
check("пустой ответ не превращается в выдуманный порядок",
      coerce_order({}, 3) == [] and coerce_order({"order": "ага"}, 3) == [])
body = render_items([{"no": 1, "title": "заголовок", "hook": "зацепка",
                      "text": "речь"}])
check("фрагмент подаётся с номером, заголовком и текстом",
      "[1]" in body and "заголовок" in body and "речь" in body)

print("\n--- сила по сравнениям ---")
power = C.strengths(3, [(0, 1), (0, 2), (1, 2)])
check("побеждающий сильнее проигрывающего", power[0] > power[1] > power[2],
      "%.2f > %.2f > %.2f" % tuple(power))
same = C.strengths(2, [(0, 1), (1, 0)])
check("ничья по кругу даёт равную силу", abs(same[0] - same[1]) < 1e-6)
lonely = C.strengths(3, [(0, 1)])
check("не участвовавший в сравнениях не получает силы из воздуха",
      lonely[2] < lonely[0])

print("\n--- порядок кандидатов ---")
transcript, cands = make(12)
truth = {c.title: n for n, c in enumerate(cands)}
mixed = [cands[n] for n in (7, 2, 11, 0, 5, 9, 1, 8, 3, 10, 4, 6)]
pairs = [(c, n + 1) for n, c in enumerate(mixed)]
cfg = Config()
cfg.compare_rounds = 3
cfg.compare_group = 4
judge = Judge(truth)
seen = []
ranked, power = C.rank(pairs, transcript, cfg, judge, verbose=False,
                       on_duel=lambda a, b: seen.append((a, b)))
check("равные оценки больше не оставляют порядок случайным",
      [c.title for c, _ in ranked][:3] == ["фрагмент 0", "фрагмент 1",
                                           "фрагмент 2"],
      [c.title for c, _ in ranked][:3])
check("сила посчитана для каждого кандидата", len(power) == len(pairs))
check("пары записаны наружу", len(seen) > 0, "%d пар" % len(seen))
named = {i: c.title for c, i in pairs}
check("в паре первым идёт победитель",
      all(truth[named[a]] < truth[named[b]] for a, b in seen))

print("\n--- отказы движка ---")
cfg2 = Config()
cfg2.compare_rounds = 1
cfg2.compare_group = 4
broken = Judge(truth, fail_every=1)
same_order, empty = C.rank(pairs, transcript, cfg2, broken, verbose=False)
check("движок молчит — остаёмся на оценках, а не падаем",
      [c.title for c, _ in same_order] == [c.title for c, _ in pairs])
check("силы при отказе никто не выдумывает", empty == {})

flaky = Judge(truth, fail_every=3)
part, some = C.rank(pairs, transcript, cfg, flaky, verbose=False)
check("часть сравнений сорвалась — порядок всё равно построен", bool(some))


class Mute:
    """Отвечает пустотой: так ведёт себя модель, не понявшая формат."""

    def compare_group(self, items, cfg):
        return []


heard = []
mute_order, mute_power = C.rank(pairs, transcript, cfg, Mute(), verbose=False,
                                on_duel=lambda a, b: heard.append((a, b)))
check("пустые ответы не рождают выдуманных пар", heard == [])
check("после пустых ответов порядок остаётся от оценок",
      [c.title for c, _ in mute_order] == [c.title for c, _ in pairs]
      and mute_power == {})

print("\n--- выключатель и мелочи ---")
off = Config()
off.compare = False
kept, none = C.rank(pairs, transcript, off, Judge(truth), verbose=False)
check("выключенное сравнение не трогает порядок",
      [c.title for c, _ in kept] == [c.title for c, _ in pairs] and none == {})
short, _ = C.rank(pairs[:2], transcript, cfg, Judge(truth), verbose=False)
check("двух кандидатов сравнивать незачем", len(short) == 2)
cfg3 = Config()
cfg3.compare_max = 5
cfg3.compare_rounds = 1
head, _ = C.rank(pairs, transcript, cfg3, Judge(truth), verbose=False)
check("хвост сверх предела остаётся на месте",
      [c.title for c, _ in head[5:]] == [c.title for c, _ in pairs[5:]])

stopped, _ = C.rank(pairs, transcript, cfg, Judge(truth), verbose=False,
                    cancel=lambda: True)
check("остановка пользователем не рушит список", len(stopped) == len(pairs))

print("\n--- база помнит сравнения ---")
box = tempfile.mkdtemp()
store = Store(Path(box) / "clips.db")
video = store.upsert_video(Path("стрим.mp4"), 120.0, "ru")
store.save_transcript(video, transcript)
ids = store.save_candidates(video, cands, "судья")
store.add_comparison(ids[0], ids[1], ids[0], source=MODEL)
store.save_power({ids[0]: 2.5, ids[1]: 0.4})
rows = {r["id"]: r for r in store.list_candidates(video)}
check("сила легла в базу", abs(rows[ids[0]]["power"] - 2.5) < 1e-6)
check("сравнения движка отделимы от ручных",
      store.count_source(MODEL)[0] == 1 and store.count_source("рука")[0] == 0)
ordered = C.by_power([rows[ids[1]], rows[ids[0]]])
check("порядок по силе, а не по оценке",
      [r["id"] for r in ordered] == [ids[0], ids[1]])
check("кандидат без сравнений уходит вниз, а не наверх",
      C.by_power([rows[ids[2]], rows[ids[1]]])[0]["id"] == ids[1])
store.close()

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
