# -*- coding: utf-8 -*-
"""Этап 6: чистка диска, место перед скачиванием, ролики по своему расписанию, уведомления в трей.

Файлы настоящие, но крошечные; время подставное. Выгрузка подставная.
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

work = tempfile.mkdtemp(prefix="cleanup_test_")
_os.environ["ASTRA_PROFILES"] = _os.path.join(work, "profiles")

from core import autopilot as ap
from core import cleanup as cl
from core import profiles as _pro
from core import service as svc

ok = []
DAY = 86400.0
NOW = 1795000000.0


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


def touch(path, age=0.0):
    _os.makedirs(_os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x" * 1000)
    _os.utime(path, (NOW - age, NOW - age))
    return path


_pro.save({"name": "стример", "created": time.time(), "face": "", "keep_days": 3})
profile = _pro.load("стример")
streams_dir = _os.path.join(work, "стримы")
ap.save_settings(profile, {"twitch": "somestreamer", "folder": streams_dir})
profile = _pro.load("стример")
own = _os.path.join(profile["folder"], ap.FOLDER)

print("записи стримов")
cases = {
    "решено, 3 дня": (1, 3, None),
    "ждёт решения, 3 дня": (1, 3, ap.READY),
    "ждёт решения, 8 дней": (1, 8, ap.APPROVED),
    "свежая, 1 день": (1, 1, None),
    "ваша, не служба": (0, 30, None),
}
paths = {}
link = ap.connect(profile)
for number, (name, (ours, days, waiting)) in enumerate(cases.items()):
    path = touch(_os.path.join(streams_dir, "стрим %d [v%d].mp4" % (number, 100 + number)))
    touch(path + ".speech.json")
    paths[name] = path
    cursor = link.execute("INSERT INTO streams (vod, url, title, path, status, found_at, done_at,"
                          " ours) VALUES (?, 'u', ?, ?, ?, ?, ?, ?)",
                          (str(100 + number), name, path, ap.DONE, NOW - days * DAY,
                           NOW - days * DAY, ours))
    if waiting:
        link.execute("INSERT INTO queue (stream_id, file, title, start, finish, status, kind)"
                     " VALUES (?, 'x.mp4', 't', 0, 1, ?, ?)", (cursor.lastrowid, waiting, ap.CLIP))
rejected = touch(_os.path.join(own, "20261110_1", "отклонён.mp4"))
posted = touch(_os.path.join(own, "20261110_1", "выложен.mp4"))
outside = touch(_os.path.join(work, "чужое", "отклонён снаружи.mp4"))
for path, status, days in ((rejected, ap.REJECTED, 3), (posted, ap.POSTED, 3),
                           (outside, ap.REJECTED, 30)):
    link.execute("INSERT INTO queue (stream_id, file, title, start, finish, status, decided_at,"
                 " posted_at, kind) VALUES (1, ?, 't', 0, 1, ?, ?, ?, ?)",
                 (path, status, NOW - days * DAY,
                  NOW - days * DAY if status == ap.POSTED else None, ap.CLIP))
link.commit()
link.close()
live_dir = _os.path.join(streams_dir, "_эфир")
old_live = touch(_os.path.join(live_dir, "somestreamer_старый.m4a"), age=8 * DAY)
new_live = touch(_os.path.join(live_dir, "somestreamer_свежий.m4a"), age=1 * DAY)
mine_live = touch(_os.path.join(live_dir, "заметки.txt"), age=30 * DAY)

said = []
report = cl.sweep(profile, now=NOW, log=said.append)
gone = {name: not _os.path.exists(path) for name, path in paths.items()}
check("решённая запись старше 2 дней удалена, с файлами рядом",
      gone["решено, 3 дня"] and not _os.path.exists(paths["решено, 3 дня"] + ".speech.json"))
check("есть нерешённое — запись живёт", not gone["ждёт решения, 3 дня"])
check("но не дольше недели", gone["ждёт решения, 8 дней"])
check("свежая запись живёт", not gone["свежая, 1 день"])
check("ваш файл, не скачанный службой, не тронут", not gone["ваша, не служба"])
check("отклонённый клип старше 2 дней удалён, выложенный 3 дня — ещё нет",
      not _os.path.exists(rejected) and _os.path.exists(posted))
check("файл вне папки автопилота не тронут, даже отклонённый", _os.path.exists(outside))
check("звук эфира старше недели удалён, свежий и чужой файл — нет",
      not _os.path.exists(old_live) and _os.path.exists(new_live) and _os.path.exists(mine_live))
check("в журнале сказано, что удалено", any("удалена запись стрима" in s for s in said))
again = cl.sweep(profile, now=NOW)
check("второй раз ничего не удаляет", again["files"] == 0)
rows = {row["title"]: row for row in ap.streams(profile)}
check("запись в базе осталась, помечена удалённой",
      rows["решено, 3 дня"]["removed_at"] == NOW and rows["ваша, не служба"]["removed_at"] is None)

print("кеши")
cache = _os.path.join(work, "ave_cache")
old_cache = touch(_os.path.join(cache, "raw_abc.s16"), age=10 * DAY)
model = touch(_os.path.join(cache, "wespeaker.onnx"), age=60 * DAY)
fresh_cache = touch(_os.path.join(cache, "voice_x.npz"), age=1 * DAY)
cl.sweep_caches(now=NOW, folder=cache)
check("старый кеш удалён, свежий и модель голосов — нет",
      not _os.path.exists(old_cache) and _os.path.exists(model) and _os.path.exists(fresh_cache))

print("место перед скачиванием")
free = [10.0]
real_free = ap.free_gb
ap.free_gb = lambda path: free[0]
swept = []
real_sweep = cl.sweep
cl.sweep = lambda profile, log=None, **k: swept.append(profile["name"]) or {"files": 0, "bytes": 0}
link = ap.connect(profile)
link.execute("INSERT INTO streams (vod, url, title, status, found_at) VALUES ('555', 'u', 'новый',"
             " ?, ?)", (ap.NEW, NOW))
link.commit()
link.close()
fetched = []
logs = []
pilot = ap.Pilot(profile, log=logs.append, lister=lambda c, l: [],
                 fetcher=lambda stream: fetched.append(stream["vod"]) or "x.mp4",
                 finder=lambda path: [], planner=lambda s, p: [])
result = pilot.run(post=False)
check("мало места — сначала чистка, запись не качается",
      swept == ["стример"] and fetched == [] and result["errors"]
      and "свободно" in result["errors"][0], str(result["errors"]))
check("стрим ждёт следующего раза, а не сломан",
      [s["status"] for s in ap.streams(profile) if s["vod"] == "555"] == [ap.NEW])
free[0] = 500.0
pilot.run(post=False)
check("место есть — качается", fetched == ["555"])
check("скачанное службой помечено как её", [s["ours"] for s in ap.streams(profile)
                                             if s["vod"] == "555"] == [1])
ap.free_gb, cl.sweep = real_free, real_sweep

print("ролики по своему расписанию")
link = ap.connect(profile)
link.execute("DELETE FROM queue")
for kind, title in ((ap.CLIP, "клип 1"), (ap.CLIP, "клип 2"), (ap.ROLIK, "Стример смотрит: А"),
                    (ap.ROLIK, "Стример играет в Б")):
    link.execute("INSERT INTO queue (stream_id, file, title, start, finish, status, kind) VALUES"
                 " (1, ?, ?, 0, 60, ?, ?)", (posted, title, ap.APPROVED, kind))
link.commit()
link.close()
uploads = []
poster = ap.Pilot(profile, lister=lambda c, l: [],
                  uploader=lambda item, title: uploads.append((item["kind"], title)) or {})
poster.post_next(now=NOW, kind=ap.ROLIK)
check("первый ролик выложен под своим названием", uploads == [(ap.ROLIK, "Стример смотрит: А")])
check("второй ролик — только через сутки",
      poster.post_next(now=NOW + 12 * 3600, kind=ap.ROLIK) is None
      and poster.post_next(now=NOW + 24.1 * 3600, kind=ap.ROLIK) is not None)
check("клипы идут своим чередом, ролики их не задерживают",
      poster.post_next(now=NOW + 1, kind=ap.CLIP) is not None and uploads[-1][0] == ap.CLIP)
sent = {}
import core.upload as _up

real_upload, real_browser, real_record = _up.upload, _up.browser, _up.record
_up.upload = lambda service, account, file, title, **k: sent.update(service=service, **k) or {}
_up.browser = lambda: "есть"
_up.record = lambda *a, **k: 0
ap.Pilot(profile, lister=lambda c, l: [])._upload(
    {"file": posted, "kind": ap.ROLIK, "start": 0.0, "finish": 60.0}, "Р")
check("ролик — на YouTube в ограниченном доступе",
      sent["service"] == "youtube" and sent["visibility"] == "private")
_up.upload, _up.browser, _up.record = real_upload, real_browser, real_record

print("служба")
notes = []
link = ap.connect(profile)
link.execute("UPDATE queue SET status = ? WHERE kind = ?", (ap.APPROVED, ap.ROLIK))
link.execute("UPDATE queue SET posted_at = NULL")
link.commit()
link.close()
service = svc.Service(log=lambda text: None, notify=notes.append,
                      watcher=lambda p, c: type("W", (), {"finishes": 0, "finished": None,
                                                          "current": None, "start": lambda s: 0,
                                                          "stop": lambda s: 0})(),
                      pilot=lambda p: None, profiles=lambda: [_pro.load("стример")],
                      clock=lambda: NOW, news=lambda p, c: [])
service.refresh()
check("одобренный ролик к выгрузке и чистка раз в час",
      ("post_rolik", "стример") in service.pending and ("clean", "") in service.pending,
      str(service.pending))
service.notify("⚠ стример: что-то сломалось")
check("уведомление уходит в трей", notes[-1].startswith("⚠"))

shutil.rmtree(work, ignore_errors=True)
print()
print("итого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
