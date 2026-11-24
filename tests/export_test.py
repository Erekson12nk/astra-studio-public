# -*- coding: utf-8 -*-
"""Выгрузка моментов: один стрим на файл, а не все вперемешку."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import argparse
import shutil
import sys
import time
import tempfile
from pathlib import Path

ROOT = _REPO
sys.path.insert(0, ROOT)

from clipper.cli import cmd_export, cmd_list
from clipper.config import Config
from clipper.store import Store

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


work = Path(tempfile.mkdtemp(prefix="export_test_"))
cfg = Config(work_dir=work, out_dir=work / "out")
cfg.ensure_dirs()
store = Store(cfg.db_path)


def add_video(path, duration, moments):
    vid = store.upsert_video(Path(path), duration, "ru")
    for start, end, title, score in moments:
        store.db.execute(
            "INSERT INTO candidates (video_id, start, end, title, model_score,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (vid, start, end, title, score, time.time()))
    store.db.commit()
    return vid


old = add_video(work / "старый.mp4", 3600.0,
                [(10.0, 25.0, "старый момент %d" % i, 10.0) for i in range(5)])
new = add_video(work / "новый.mp4", 3600.0,
                [(100.0, 130.0, "новый момент %d" % i, 4.0) for i in range(4)])

check("в базе два стрима", store.videos_with_candidates() == 2)
check("последним числится свежий", store.latest_video()["id"] == new,
      store.latest_video()["id"])


def export(video, top=10):
    out = work / "tc.txt"
    args = argparse.Namespace(video=video, out=str(out), top=top, min_score=0.0)
    code = cmd_export(args, cfg, store)
    body = out.read_text(encoding="utf-8") if out.exists() else ""
    return code, body


# --- без --video берётся последний обработанный, а не всё подряд ---
code, body = export(None)
check("выгрузка прошла", code == 0)
check("взят свежий стрим", "новый момент" in body)
check("старый стрим не подмешан", "старый момент" not in body, body[:200])
check("моментов ровно столько, сколько есть у свежего",
      body.count("новый момент") == 4, body.count("новый момент"))

# --- старые кандидаты выше по оценке, но они не должны перетягивать ---
check("высокая оценка чужого стрима не перетягивает",
      "старый момент" not in body)

# --- явный --video по-прежнему работает ---
code, body = export(old)
check("по --video берётся заказанный стрим",
      code == 0 and "старый момент" in body and "новый момент" not in body)

# --- пустая база отвечает внятно ---
empty_dir = Path(tempfile.mkdtemp(prefix="export_empty_"))
empty_cfg = Config(work_dir=empty_dir, out_dir=empty_dir / "out")
empty_cfg.ensure_dirs()
empty = Store(empty_cfg.db_path)
args = argparse.Namespace(video=None, out=str(empty_dir / "tc.txt"), top=10,
                          min_score=0.0)
check("на пустой базе не падает, а объясняет",
      cmd_export(args, empty_cfg, empty) == 1)

# --- list предупреждает, что показывает вперемешку ---
args = argparse.Namespace(video=None, rerank=False, no_rerank=True)
check("list на нескольких стримах не падает",
      cmd_list(args, cfg, store) == 0)

# --- равные оценки не отдаются началу стрима ---
from clipper import scoring as sc

flat = [{"start": i * 60.0, "id": i} for i in range(120)]
picked = sc.spread(flat, 20, 120 * 60.0)
check("из равных берётся ровно сколько просили", len(picked) == 20, len(picked))
half = sum(1 for r in picked if r["start"] >= 60 * 60.0)
check("вторая половина стрима не выброшена", half >= 8, half)
buckets = {int(r["start"] // (120 * 60.0 / 8)) for r in picked}
check("охвачены все восемь частей стрима", len(buckets) == 8, sorted(buckets))

# --- порядок по оценке уважается: лучшее берётся первым ---
mixed = ([{"start": 7000.0 + i, "id": 900 + i} for i in range(5)]
         + [{"start": float(i), "id": i} for i in range(50)])
top5 = sc.spread(mixed, 5, 7200.0)
check("лучшие не теряются из-за разноса",
      sum(1 for r in top5 if r["id"] >= 900) >= 1, [r["id"] for r in top5])

# --- когда просят больше, чем есть, отдаётся всё ---
check("просят больше, чем есть — отдаётся всё",
      len(sc.spread(flat, 500, 7200.0)) == len(flat))
check("ноль просят — ноль дают", sc.spread(flat, 0, 7200.0) == [])
check("без длительности не падает", len(sc.spread(flat, 10, 0.0)) == 10)

# --- пустые места добираются лучшими, а не оставляются пустыми ---
lop = [{"start": 10.0 + i, "id": i} for i in range(40)]
check("когда материал только в начале, мест не теряется",
      len(sc.spread(lop, 20, 7200.0)) == 20)

shutil.rmtree(work, ignore_errors=True)
shutil.rmtree(empty_dir, ignore_errors=True)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
