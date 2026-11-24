# -*- coding: utf-8 -*-
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from core import ffmpeg as _ff
from core import rerank as R
from clipper import store as _st
from clipper.cli import candidate_rows, cmd_picks
from clipper.config import Config
from clipper.models import Sentence, Transcript
from clipper.store import Store

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


work = Path(tempfile.mkdtemp(prefix="missed_test_"))
cfg = Config(work_dir=work, out_dir=work / "out")
cfg.ensure_dirs()
store = Store(cfg.db_path)

vid = store.upsert_video(work / "стрим.mp4", 7200.0, "ru")
sentences = [Sentence(idx=i, start=i * 10.0, end=i * 10.0 + 9.0,
                      text="раз два три четыре пять")
             for i in range(720)]
store.save_transcript(vid, Transcript(sentences=sentences, language="ru",
                                      duration=7200.0))
for i in range(20):
    store.db.execute(
        "INSERT INTO candidates (video_id, start, end, title, model_score,"
        " features_json, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (vid, i * 180.0, i * 180.0 + 30.0, "момент %d" % i, float(i % 7 + 3),
         json.dumps({"duration": 30.0, "n_sentences": 3, "chars": 70,
                     "words_per_sec": 0.5}), "эвристика", time.time()))
store.db.commit()
before = store.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]

mine = work / "мои"
mine.mkdir()


def clip(t, name="Момент"):
    path = mine / ("%02d-%02d-%02d_%s.mp4" % (t // 3600, t % 3600 // 60, t % 60, name))
    path.write_bytes(b"x")
    return path


clip(0)
clip(5 * 180)
clip(6000, "промах")
clip(6800, "битый")
lengths = {"промах": 45.0, "битый": 0.0}
_ff.duration = lambda p: lengths.get(Path(p).stem.split("_", 1)[1], 30.0)


def run(**kw):
    args = argparse.Namespace(folder=str(mine), video=None, slack=90.0,
                              pairs=5, seed=1, forget=False)
    for k, v in kw.items():
        setattr(args, k, v)
    return cmd_picks(args, cfg, store)


check("перенос прошёл", run() == 0)
hand = store.db.execute("SELECT * FROM candidates WHERE model = ?",
                        (_st.HAND,)).fetchall()
check("добавлен ровно один пропущенный", len(hand) == 1, len(hand))
row = hand[0]
check("время взято из имени и длины файла",
      row["start"] == 6000.0 and row["end"] == 6045.0, (row["start"], row["end"]))
feats = json.loads(row["features_json"])
check("длительность в признаках", feats["duration"] == 45.0, feats)
check("предложения посчитаны по расшифровке", feats["n_sentences"] == 5, feats)
check("символы посчитаны", feats["chars"] == 5 * len(sentences[0].text), feats)
check("темп речи посчитан", abs(feats["words_per_sec"] - 25 / 45.0) < 1e-9, feats)
check("оценка движка — медиана, а не ноль", row["model_score"] == 6.0,
      row["model_score"])

wins = store.db.execute(
    "SELECT left_id, right_id, winner_id FROM comparisons WHERE source = ?",
    (_st.PICKS,)).fetchall()
mine_wins = [w for w in wins if w["winner_id"] == row["id"]]
check("пропущенный побеждает невзятых", len(mine_wins) == 5, len(mine_wins))
check("всего сравнений по трём клипам", len(wins) == 15, len(wins))

check("повтор узнаёт пропущенный, не плодит", run() == 0)
again = store.db.execute("SELECT COUNT(*) FROM candidates WHERE model = ?",
                         (_st.HAND,)).fetchone()[0]
check("пропущенный по-прежнему один", again == 1, again)
losers = {w["right_id"] for w in store.db.execute(
    "SELECT right_id FROM comparisons WHERE source = ?", (_st.PICKS,))}
check("ручной не попадает в проигравшие", row["id"] not in losers)

rows = candidate_rows(store)
check("ручной помечен в строках обучения",
      [r["hand"] for r in rows if r["id"] == row["id"]] == [True])
fill = R.engine_fill(rows)
check("заполнитель — медиана движка", fill == 6.0, fill)
vec = {r["id"]: R.feature_vector(r, fill) for r in rows}
check("ручному подставлена медиана", vec[row["id"]][0] == 6.0, vec[row["id"]])

comparisons = [(r["winner_id"], r["right_id"]) for r in store.db.execute(
    "SELECT right_id, winner_id FROM comparisons WHERE winner_id IS NOT NULL")]
model, report = R.train(rows, {}, comparisons)
check("модель обучилась", model is not None, report)
check("пары с ручным вошли в обучение",
      sum(1 for w, _ in comparisons if w == row["id"]) >= 5)
check("заполнитель сохранён в модели", model["fill"] == 6.0, model.get("fill"))
blank = dict(next(r for r in rows if r["id"] == row["id"]))
blank["model_score"] = 0.0
check("оценка ручного не зависит от записанного числа",
      R.score(model, blank) == R.score(model, next(r for r in rows if r["id"] == row["id"])))

check("снятие выведенного прошло", run(forget=True) == 0)
left = store.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
check("пропущенные убраны вместе с выведенным", left == before, (left, before))

store.close()
shutil.rmtree(work, ignore_errors=True)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
