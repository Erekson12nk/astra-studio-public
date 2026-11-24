# -*- coding: utf-8 -*-
"""Ваш выбор как обучающий сигнал: выведенное помечено и снимается."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import argparse
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = _REPO
sys.path.insert(0, ROOT)

from clipper import store as _st
from clipper.cli import cmd_picks
from clipper.config import Config
from clipper.store import Store

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


work = Path(tempfile.mkdtemp(prefix="picks_test_"))
cfg = Config(work_dir=work, out_dir=work / "out")
cfg.ensure_dirs()
store = Store(cfg.db_path)

vid = store.upsert_video(work / "стрим.mp4", 7200.0, "ru")
for i in range(40):
    store.db.execute(
        "INSERT INTO candidates (video_id, start, end, title, model_score,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (vid, i * 180.0, i * 180.0 + 30.0, "момент %d" % i, 9.0, time.time()))
store.db.commit()

# то, что человек уже наставил руками
store.db.execute("INSERT INTO ratings (candidate_id, rating, created_at)"
                 " VALUES (1, 5, ?)", (time.time(),))
store.add_comparison(1, 2, 1)
store.db.commit()
hand_before = store.count_source(_st.HAND)
check("ручные записи помечены как ручные", hand_before == (1, 1), hand_before)

# папка с «моими» клипами: таймкод зашит в имя
mine = work / "мои"
mine.mkdir()
for i in (0, 5, 10, 15, 20):
    t = int(i * 180)
    mine.joinpath("%02d_%02d-%02d-%02d_Момент.mp4"
                  % (i, t // 3600, t % 3600 // 60, t % 60)).write_bytes(b"x")


def run(**kw):
    args = argparse.Namespace(folder=str(mine), video=None, slack=90.0,
                              pairs=3, seed=1, forget=False)
    for k, v in kw.items():
        setattr(args, k, v)
    return cmd_picks(args, cfg, store)


check("перенос прошёл", run() == 0)
made = store.count_source(_st.PICKS)
check("сравнения выведены", made[0] == 5 * 3, made)
check("оценок за человека не выставлено", made[1] == 0, made)
check("ручные записи не тронуты", store.count_source(_st.HAND) == hand_before,
      store.count_source(_st.HAND))

rows = store.db.execute(
    "SELECT left_id, right_id, winner_id FROM comparisons WHERE source = ?",
    (_st.PICKS,)).fetchall()
check("во всех парах побеждает выбранный вами",
      all(r["winner_id"] == r["left_id"] for r in rows))
picked_ids = {r["winner_id"] for r in rows}
check("узнано ровно пять ваших клипов", len(picked_ids) == 5, sorted(picked_ids))
check("сам с собой никто не сравнивается",
      all(r["left_id"] != r["right_id"] for r in rows))
losers = {r["right_id"] for r in rows}
check("в проигравших нет ваших", not (losers & picked_ids), sorted(losers & picked_ids))

# --- повтор не удваивает, потому что --forget убирает начисто ---
check("снятие выведенного прошло", run(forget=True) == 0)
check("выведенного не осталось", store.count_source(_st.PICKS) == (0, 0),
      store.count_source(_st.PICKS))
check("ручное пережило снятие", store.count_source(_st.HAND) == hand_before,
      store.count_source(_st.HAND))

# --- пустая папка и промахи не роняют ---
empty = work / "пусто"
empty.mkdir()
check("пустая папка отвечает внятно", run(folder=str(empty)) == 1)
check("несуществующая папка отвечает внятно",
      run(folder=str(work / "нет-такой")) == 1)

# --- старая база без колонки source мигрирует, а не падает ---
legacy = work / "старая.sqlite3"
raw = sqlite3.connect(legacy)
raw.executescript(
    "CREATE TABLE ratings (id INTEGER PRIMARY KEY, candidate_id INTEGER,"
    " rating INTEGER NOT NULL, created_at REAL NOT NULL);"
    "CREATE TABLE comparisons (id INTEGER PRIMARY KEY, left_id INTEGER,"
    " right_id INTEGER, winner_id INTEGER, created_at REAL NOT NULL);"
    "INSERT INTO ratings (candidate_id, rating, created_at) VALUES (1, 4, 0);")
raw.commit()
raw.close()
old = Store(legacy)
check("старая база открылась", old.count_source(_st.HAND) == (0, 1),
      old.count_source(_st.HAND))
check("прежняя оценка не потерялась и считается ручной",
      old.db.execute("SELECT source FROM ratings").fetchone()["source"] == _st.HAND)
old.close()

store.close()
shutil.rmtree(work, ignore_errors=True)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
