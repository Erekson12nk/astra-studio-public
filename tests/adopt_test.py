# -*- coding: utf-8 -*-
"""Клипы из astra возвращаются в базу clipper и становятся видны в оценке."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import os, sys, tempfile

ROOT = _REPO
sys.path.insert(0, ROOT)

from core import handoff as H

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


# --- таймкод из автоимени astra ---
check("час-минута-секунда разбирается",
      H.start_from_name("01_02-51-13_Ебать_я_минусанул.mp4") == 2 * 3600 + 51 * 60 + 13,
      H.start_from_name("01_02-51-13_Ебать_я_минусанул.mp4"))
check("нули в начале не мешают",
      H.start_from_name("07_00-27-58_Анимешные_модели.mp4") == 27 * 60 + 58)
check("без таймкода — ничего", H.start_from_name("клип.mp4") is None)
check("чужие цифры не принимаются за время",
      H.start_from_name("видео_2026-08-31.mp4") is None,
      H.start_from_name("видео_2026-08-31.mp4"))
check("минуты больше 59 отвергаются",
      H.start_from_name("01_00-77-13_что-то.mp4") is None)


class Row(dict):
    def __getitem__(self, key):
        return dict.__getitem__(self, key)


rows = [Row(id=1, start=100.0), Row(id=2, start=340.0), Row(id=3, start=900.0)]

# --- поиск кандидата с допуском ---
found, gap = H.match_candidate(341.0, rows)
check("клип привязывается к ближайшему кандидату", found["id"] == 2, gap)
check("расхождение считается", abs(gap - 1.0) < 1e-6, gap)

far, _ = H.match_candidate(500.0, rows)
check("далёкий клип не привязывается", far is None)

near, _ = H.match_candidate(346.0, rows, slack=6.0)
check("на границе допуска ещё привязывается", near["id"] == 2)
outside, _ = H.match_candidate(347.0, rows, slack=6.0)
check("за границей допуска уже нет", outside is None)

# --- целиком через базу ---
import sqlite3
import time

from clipper.config import Config
from clipper.store import Store

work = tempfile.mkdtemp()
os.environ["CLIPPER_WORK"] = work
cfg = Config()
cfg.work_dir = __import__("pathlib").Path(work)
cfg.ensure_dirs()
store = Store(cfg.db_path)
from pathlib import Path

video = Path(work) / "стрим.mp4"
video.write_bytes(b"x" * 32)
vid = store.upsert_video(video, 7200.0, "ru")

from clipper.models import Candidate

cands = [Candidate(start=10273.0, end=10296.0, title="Первый", hook="", reason="",
                   score=9.0),
         Candidate(start=306.0, end=341.0, title="Второй", hook="", reason="",
                   score=8.0)]
ids = store.save_candidates(vid, cands, "тест")

out = Path(work) / "из-астры"
out.mkdir()
(out / "01_02-51-13_Первый.mp4").write_bytes(b"v")
(out / "02_00-05-06_Второй.mp4").write_bytes(b"v")
(out / "03_безымянный.mp4").write_bytes(b"v")

from clipper import cli as C


class Args:
    folder = str(out)
    video = None
    slack = 6.0


code = C.cmd_adopt(Args(), cfg, store)
check("adopt отработал", code == 0)
index = store.render_index()
check("оба клипа с таймкодом привязаны",
      all(cid in index for cid in ids), sorted(index))
check("файл без таймкода не привязан", len(index) == 2, len(index))
check("привязан именно тот файл",
      os.path.basename(index[ids[0]]["path"]).startswith("01_"),
      os.path.basename(index[ids[0]]["path"]))

before = len(store.db.execute("SELECT 1 FROM renders").fetchall())
C.cmd_adopt(Args(), cfg, store)
after = len(store.db.execute("SELECT 1 FROM renders").fetchall())
check("повторный запуск не плодит записи", before == after, "%d → %d" % (before, after))

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
