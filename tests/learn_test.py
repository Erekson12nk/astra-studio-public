# -*- coding: utf-8 -*-
"""Связывание баз: кандидаты clipper <-> опубликованное в astra."""
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

from media import build as _build, CACHE as _CACHE

_MEDIA = _build()
_CACHE_U = _CACHE.replace("\\", "/")

import os, sqlite3, sys, tempfile, time

ROOT = _REPO
sys.path.insert(0, ROOT)

from core import learning as L

ok = []


def check(label, value, extra=""):
    ok.append(bool(value))
    print("  %-54s %s%s" % (label, "ок" if value else "ПРОВАЛ",
                            ("  " + str(extra)) if extra else ""))


work = tempfile.mkdtemp()
stream = os.path.join(work, "стрим 2026.mp4")
open(stream, "wb").write(b"x" * 100)
other = os.path.join(work, "другой.mp4")
open(other, "wb").write(b"y" * 100)

# --- база clipper ---
cdb = os.path.join(work, "clipper.sqlite3")
c = sqlite3.connect(cdb)
c.executescript("""
CREATE TABLE videos (id INTEGER PRIMARY KEY, path TEXT UNIQUE, duration REAL,
                     language TEXT, created_at REAL);
CREATE TABLE candidates (id INTEGER PRIMARY KEY, video_id INTEGER, start REAL,
                         end REAL, title TEXT, hook TEXT, reason TEXT,
                         model_score REAL, features_json TEXT, model TEXT,
                         created_at REAL);
CREATE TABLE ratings (id INTEGER PRIMARY KEY, candidate_id INTEGER, rating INTEGER,
                      created_at REAL);
CREATE TABLE comparisons (id INTEGER PRIMARY KEY, left_id INTEGER, right_id INTEGER,
                          winner_id INTEGER, created_at REAL);
""")
c.execute("INSERT INTO videos VALUES (1,?,7200,'ru',?)", (stream, time.time()))
c.execute("INSERT INTO videos VALUES (2,?,3600,'ru',?)", (other, time.time()))
cands = [
    (1, 1, 100.0, 140.0, "Про рыбалку", 8.4),
    (2, 1, 500.0, 560.0, "Спор с чатом", 6.1),
    (3, 1, 900.0, 950.0, "Никем не взятый", 7.7),
    (4, 2, 10.0, 40.0, "Из другого стрима", 9.0),
]
for cid, vid, s, e, title, score in cands:
    c.execute("INSERT INTO candidates (id,video_id,start,end,title,model_score,"
              "model,created_at) VALUES (?,?,?,?,?,?,?,?)",
              (cid, vid, s, e, title, score, "heuristic", time.time()))
c.execute("INSERT INTO ratings (candidate_id, rating, created_at) VALUES (1,5,?)",
          (time.time(),))
c.commit(); c.close()

# --- база astra ---
adb = os.path.join(work, "clips.db")
a = sqlite3.connect(adb)
a.executescript("""
CREATE TABLE clips (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, origin TEXT,
    start REAL, finish REAL, score REAL, reason TEXT, file TEXT, status TEXT,
    made_at REAL, ready_at REAL);
CREATE TABLE posts (id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id INTEGER,
    service TEXT, video_id TEXT, url TEXT, privacy TEXT, posted_at REAL,
    dry INTEGER DEFAULT 0);
CREATE TABLE stats (id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, at REAL,
    views INTEGER, likes INTEGER, comments INTEGER);
""")
# тот же момент, границы чуть уехали после уточнения по паузам
a.execute("INSERT INTO clips (id,source,origin,start,finish,score,file,status)"
          " VALUES (1,?,'смысл',102.0,139.0,8.4,'рыбалка.mp4','готов')", (stream,))
a.execute("INSERT INTO clips (id,source,origin,start,finish,score,file,status)"
          " VALUES (2,?,'смысл',498.0,562.0,6.1,'спор.mp4','готов')", (stream,))
# смонтирован, но не опубликован
a.execute("INSERT INTO clips (id,source,origin,start,finish,score,file,status)"
          " VALUES (3,?,'громкость',900.0,950.0,7.7,'третий.mp4','готов')", (stream,))
# опубликован, но такого кандидата нет
a.execute("INSERT INTO clips (id,source,origin,start,finish,score,file,status)"
          " VALUES (4,?,'вручную',3000.0,3060.0,0,'ручной.mp4','готов')", (stream,))
for cid, vid in ((1, "yt_aaa"), (2, "yt_bbb"), (4, "yt_zzz")):
    a.execute("INSERT INTO posts (clip_id,service,video_id,url,posted_at,dry)"
              " VALUES (?,?,?,?,?,0)",
              (cid, "youtube", vid, "https://y/" + vid, time.time()))
# черновик не считается
a.execute("INSERT INTO posts (clip_id,service,video_id,url,posted_at,dry)"
          " VALUES (3,'tiktok','tt_x','https://t/x',?,1)", (time.time(),))
a.execute("INSERT INTO stats (video_id,at,views,likes,comments)"
          " VALUES ('yt_aaa',?,15200,880,44)", (time.time(),))
a.execute("INSERT INTO stats (video_id,at,views,likes,comments)"
          " VALUES ('yt_aaa',?,31000,1500,90)", (time.time() + 60,))
a.execute("INSERT INTO stats (video_id,at,views,likes,comments)"
          " VALUES ('yt_bbb',?,420,7,1)", (time.time(),))
a.commit(); a.close()

print("--- связывание ---")
rep = L.link_databases(cdb, adb)
for key in ("candidates", "published", "matched", "written", "no_stats",
            "rendered_matched"):
    print("  %-22s %s" % (key, rep[key]))
print("  не сопоставлено       %s" % [u["file"] for u in rep["unmatched"]])

check("кандидатов прочитано", rep["candidates"] == 4)
check("опубликованных прочитано (черновик не в счёт)", rep["published"] == 3)
check("совпало два момента", rep["matched"] == 2)
check("связей записано", rep["written"] == 2)
check("ручной ролик не привязан", len(rep["unmatched"]) == 1
      and rep["unmatched"][0]["file"] == "ручной.mp4")
check("смонтирован, но НЕ опубликован — ровно один",
      rep["rendered_matched"] == 1, rep["rendered_matched"])

print("\n--- обучающий сигнал ---")
rows = L.training_signal(cdb)
for r in rows[:4]:
    print("  %-22s оценка %.1f  просмотров %s  моя оценка %s"
          % ((r["title"] or "")[:22], r["model_score"] or 0, r["views"], r["rating"]))
top = [r for r in rows if r["views"]]
check("сигнал отсортирован по просмоторам", top and top[0]["views"] == 31000)
check("взяты СВЕЖИЕ просмотры, а не первые", all(r["views"] != 15200 for r in rows))
check("кандидат без публикации остался", any(r["views"] is None for r in rows))
check("оценка человека подтянулась", any(r["rating"] == 5.0 for r in rows))

print("\n--- сводка ---")
s = L.summary(cdb)
for k, v in sorted(s.items()):
    print("  %-16s %s" % (k, v))
check("сводка считает просмотры", s["total_views"] == 31420)
check("сводка считает связанных", s["with_outcome"] == 2)

print("\n--- повторный запуск не плодит дублей ---")
rep2 = L.link_databases(cdb, adb)
s2 = L.summary(cdb)
check("связей столько же", s2["with_outcome"] == 2)
check("строк не удвоилось", sqlite3.connect(cdb).execute(
    "SELECT COUNT(*) FROM outcomes").fetchone()[0] == 2)

print("\n--- защита ---")
check("нет базы astra -> понятная ошибка",
      "astra" in L.link_databases(cdb, os.path.join(work, "нет.db")).get("error", ""))
check("нет базы clipper -> понятная ошибка",
      "clipper" in L.link_databases(os.path.join(work, "нет.db"), adb).get("error", ""))
check("порог совпадения работает",
      L.link_databases(cdb, adb, min_overlap=0.99)["matched"] == 0)

print("\n  Всего проверок: %d, провалов: %d" % (len(ok), ok.count(False)))
sys.exit(1 if ok.count(False) else 0)
