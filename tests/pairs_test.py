# -*- coding: utf-8 -*-
"""Подбор пар для оценки: свежие клипы видно, самопар нет, повторов нет."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import shutil, sys, tempfile, time
from pathlib import Path

from clipper.store import Store

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


work = Path(tempfile.mkdtemp(prefix="pairs_test_"))
store = Store(work / "clipper.sqlite3")

video = store.upsert_video(work / "стрим.mp4", 3600.0, "ru")
now = time.time()


def add(cid, score, at):
    store.db.execute(
        """INSERT INTO candidates (id, video_id, start, end, title, model_score,
                                   features_json, model, created_at)
           VALUES (?, ?, ?, ?, ?, ?, '{}', 'проба', ?)""",
        (cid, video, cid * 10.0, cid * 10.0 + 30.0, "клип %d" % cid, score, at))
    store.db.commit()
    store.db.execute(
        "INSERT INTO renders (candidate_id, path, created_at) VALUES (?, ?, ?)",
        (cid, str(work / ("%d.mp4" % cid)), at))
    store.db.commit()


# Движок насыщает оценку: всем одно и то же, как на живой базе.
старые = list(range(1, 11))
свежие = list(range(11, 21))
for cid in старые:
    add(cid, 9.0, now - 86400)
for cid in свежие:
    add(cid, 9.0, now)

pairs = store.suggest_pairs(limit=20)
участники = set()
for _gap, a, b in pairs:
    участники.add(a["id"])
    участники.add(b["id"])

check("пары нашлись", len(pairs) == 20, len(pairs))
check("клип не встаёт сам против себя",
      all(a["id"] != b["id"] for _g, a, b in pairs))
check("свежие клипы попадают в выдачу",
      any(cid in участники for cid in свежие),
      sorted(участники & set(свежие)))
check("выдача не вырождается в один клип против всех",
      len(участники) > 4, len(участники))

повторы = {}
for _gap, a, b in pairs:
    for row in (a, b):
        повторы[row["id"]] = повторы.get(row["id"], 0) + 1
check("ни один клип не занимает всю выдачу", max(повторы.values()) <= 4,
      max(повторы.values()))

# --- уже сравнённые пары не предлагаются заново ---
first = pairs[0]
store.add_comparison(first[1]["id"], first[2]["id"], first[1]["id"])
again = store.suggest_pairs(limit=20)
сравнённая = tuple(sorted((first[1]["id"], first[2]["id"])))
check("сравнённая пара больше не предлагается",
      all(tuple(sorted((a["id"], b["id"]))) != сравнённая for _g, a, b in again))

# --- клип, которого ещё не сравнивали, идёт впереди ---
for _gap, a, b in again[:5]:
    pass
counts = {}
for r in store.db.execute("SELECT left_id, right_id FROM comparisons"):
    counts[r["left_id"]] = counts.get(r["left_id"], 0) + 1
    counts[r["right_id"]] = counts.get(r["right_id"], 0) + 1
верх = again[0]
check("впереди идут те, кого ещё не сравнивали",
      counts.get(верх[1]["id"], 0) == 0 and counts.get(верх[2]["id"], 0) == 0,
      (counts.get(верх[1]["id"], 0), counts.get(верх[2]["id"], 0)))

# --- повторный рендер не плодит записи ---
store.record_render(1, work / "1_заново.mp4")
n = store.db.execute("SELECT COUNT(*) FROM renders WHERE candidate_id = 1").fetchone()[0]
check("пере-рендер заменяет запись, а не добавляет", n == 1, n)
check("путь обновился на новый",
      store.db.execute("SELECT path FROM renders WHERE candidate_id = 1"
                       ).fetchone()["path"].endswith("1_заново.mp4"))

# --- старые дубли вычищаются разом ---
store.db.execute(
    "INSERT INTO renders (candidate_id, path, created_at) VALUES (2, ?, ?)",
    (str(work / "2_дубль.mp4"), now))
store.db.commit()
before = store.db.execute("SELECT COUNT(*) FROM renders").fetchone()[0]
lost = store.drop_duplicate_renders()
after = store.db.execute("SELECT COUNT(*) FROM renders").fetchone()[0]
check("дубли вычищаются", lost == 1 and after == before - 1, (lost, before, after))
check("на чистой базе чистка ничего не трогает",
      store.drop_duplicate_renders() == 0)

# --- клипы из разных стримов не сравниваются ---
другое = store.upsert_video(work / "другой.mp4", 100.0, "ru")
store.db.execute(
    """INSERT INTO candidates (id, video_id, start, end, title, model_score,
                               features_json, model, created_at)
       VALUES (99, ?, 1.0, 30.0, 'чужой', 9.0, '{}', 'проба', ?)""", (другое, now))
store.db.commit()
store.record_render(99, work / "99.mp4")
check("клипы из разных стримов в пару не идут",
      all(99 not in (a["id"], b["id"]) or a["video_id"] == b["video_id"]
          for _g, a, b in store.suggest_pairs(limit=40)))

store.close()
shutil.rmtree(work, ignore_errors=True)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
