# -*- coding: utf-8 -*-
"""Ролики по играм: главы твича → куски игр → сборка → очередь автопилота.

Главы взяты по образцу настоящего стрима: Just Chatting, игра, вставка
«Games + Demos» на минуту, та же игра дальше. Твич подставной. Сборка ролика
настоящая — clipmaker на коротком синтетическом видео.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _REPO)

import json
import shutil
import subprocess
import tempfile
import time

import numpy as np

work = tempfile.mkdtemp(prefix="roliks_test_")
_os.environ["ASTRA_PROFILES"] = _os.path.join(work, "profiles")
_os.environ["ASTRA_STUDIO_SETTINGS"] = _os.path.join(work, "settings.json")
with open(_os.environ["ASTRA_STUDIO_SETTINGS"], "w", encoding="utf-8") as handle:
    json.dump({"fade": "0.5", "blur": "5", "crf": "30", "censor": False}, handle)

from core import autopilot as ap
from core import ffmpeg as _ff
from core import profiles as _pro
from core import roliks as rol

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


def node(start, length, game):
    return {"node": {"positionMilliseconds": int(start * 1000),
                     "durationMilliseconds": int(length * 1000), "type": "GAME_CHANGE",
                     "description": game, "details": {"game": {"displayName": game}}}}


ANSWER = {"data": {"video": {"lengthSeconds": 18000, "game": {"displayName": "Just Chatting"},
                             "moments": {"edges": [
                                 node(0, 13051, "Just Chatting"),
                                 node(13051, 3815, "Cozy Game Restoration"),
                                 node(16866, 82, "Games + Demos"),
                                 node(16948, 822, "Cozy Game Restoration"),
                                 node(17770, 229, "GeoGuessr"),
                                 node(17999, 1, "Just Chatting")]}}}}
asked = []

print("главы твича")
found = rol.chapters("2924822017", ask=lambda body: asked.append(body) or ANSWER)
check("главы прочитаны по порядку", [c["game"] for c in found][:3]
      == ["Just Chatting", "Cozy Game Restoration", "Games + Demos"]
      and found[1]["start"] == 13051 and found[1]["end"] == 13051 + 3815)
check("в запросе номер записи", "2924822017" in asked[0]["query"])
whole = rol.chapters("1", ask=lambda body: {"data": {"video": {
    "lengthSeconds": 7200, "game": {"displayName": "Minecraft"}, "moments": {"edges": []}}}})
check("одна игра за весь стрим — одна глава на всё",
      whole == [{"start": 0.0, "end": 7200.0, "game": "Minecraft"}])
check("записи нет — глав нет", rol.chapters("1", ask=lambda body: {"data": {"video": None}})
      == [])

print("куски игр")
parts = rol.game_parts(found)
check("вставка «Games + Demos» посреди игры ролик не рвёт",
      len(parts) == 1 and parts[0]["game"] == "Cozy Game Restoration"
      and parts[0]["start"] == 13051 and parts[0]["end"] == 17770, str(parts))
check("Just Chatting и короткая GeoGuessr — не ролики",
      all(p["game"] not in ("Just Chatting", "GeoGuessr") for p in parts))
check("порог длины настраивается",
      [p["game"] for p in rol.game_parts(found, min_len=180)]
      == ["Cozy Game Restoration", "GeoGuessr"])
far = [{"start": 0, "end": 2000, "game": "CS"}, {"start": 2000, "end": 4000, "game": "Just Chatting"},
       {"start": 4000, "end": 6000, "game": "CS"}]
check("та же игра после долгого перерыва — два ролика", len(rol.game_parts(far)) == 2)

print("границы")
level = np.ones(600, np.float32)
level[290:293] = 0.01
check("граница уходит в тишину рядом", abs(rol.quiet_near(level, 1.0, 320.0) - 291.5) < 1.5)
check("тишины рядом нет — граница остаётся по главе",
      rol.quiet_near(np.ones(600, np.float32), 1.0, 320.0) == 320.0)
snapped = rol.snap([{"start": 30.0, "end": 590.0, "game": "X"}],
                   {"level": level, "window": 1.0}, length=600.0)
check("начало стрима и конец стрима не двигаются",
      snapped[0]["start"] == 0.0 and snapped[0]["end"] == 590.0)
check("название ролика", rol.title_for(rol.TITLE, "Стример", "Cozy Game Restoration")
      == "Стример играет в Cozy Game Restoration")
plan = rol.plan_games("2924822017", {"name": "стример"}, ask=lambda body: ANSWER,
                      levels={"level": np.ones(18000, np.float32), "window": 1.0})
check("план: игра с названием по стримеру", len(plan) == 1
      and plan[0]["title"] == "Стример играет в Cozy Game Restoration"
      and plan[0]["start"] == 13051.0 and plan[0]["end"] == 17770.0, str(plan))

print("сборка")
source = _os.path.join(work, "stream.mp4")
subprocess.run([_ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=20",
                "-f", "lavfi", "-i", "sine=frequency=330:duration=20",
                "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", source], check=True)
setup = rol.options_from_studio()
check("настройки сборки — из окна Студии", setup["options"]["fade"] == 0.5
      and setup["options"]["crf"] == 30 and setup["censor"] is False)
made = rol.make(source, [{"start": 2.0, "end": 12.0, "title": "Стример играет в X"}],
                _os.path.join(work, "out"), setup)
video = made[0]["video"] if made else ""
length = _ff.duration(video) if video else 0.0
check("ролик собран под своим названием", video.endswith("Стример_играет_в_X.mp4")
      and abs(length - 10.0) < 1.0, "%s %.1f с" % (_os.path.basename(video), length))

print("автопилот")
_pro.save({"name": "стример", "created": time.time(), "face": "", "keep_days": 3})
profile = _pro.load("стример")
ap.save_settings(profile, {"twitch": "somestreamer", "donations": False, "away": False})
profile = _pro.load("стример")
checked, built = [], []


def checker(path, moments, donations=None, away=None):
    checked.append((len(moments), donations, away))
    return [dict(moments[0], drops=[(14000.0, 14100.0, "ушёл")])]


def planner(stream, path):
    return [dict(plan[0])]


def maker(stream, path, items):
    built.append(items)
    target = _os.path.join(work, "rolik.mp4")
    shutil.copy2(video, target)
    return [target]


def renderer(stream, path, moments):
    target = _os.path.join(work, "clip.mp4")
    shutil.copy2(video, target)
    return [target]


pilot = ap.Pilot(profile, log=lambda text: None,
                 lister=lambda c, l: [{"vod": "2924822017", "url": "u", "title": "стрим",
                                       "duration": 1.0, "started": 0.0}],
                 fetcher=lambda stream: source,
                 finder=lambda path: [{"start": 1.0, "end": 5.0, "score": 9, "title": "клип"}],
                 checker=checker, renderer=renderer, planner=planner, maker=maker)
report = pilot.run(post=False)
rows = ap.queue(profile)
kinds = sorted(r["kind"] for r in rows)
check("в очереди клип и ролик", report["queued"] == 2 and kinds == [ap.CLIP, ap.ROLIK],
      str(kinds))
check("в ролике вырезаны донаты и уходы (проверка ролика)",
      checked == [(1, True, True)] and [r["cuts"] for r in rows if r["kind"] == ap.ROLIK]
      == [1], str(checked))
check("ролик назван по игре", [r["title"] for r in rows if r["kind"] == ap.ROLIK]
      == ["Стример играет в Cozy Game Restoration"])
ap.decide(profile, [r["id"] for r in rows if r["kind"] == ap.ROLIK], ap.APPROVED)
posted = []
pilot.uploader = lambda item, title: posted.append(item) or {"url": "x"}
check("ролики не уходят в выгрузку клипов", pilot.post_next(force=True) is None
      and posted == [])
ap.save_settings(profile, {"roliks": False})
quiet = ap.Pilot(_pro.load("стример"), lister=lambda c, l: [], planner=planner, maker=maker)
check("галочка выключает ролики", quiet._roliks({"vod": "1", "id": 1}, source) == [])
check("у своей записи (не твич) глав нет — роликов нет",
      pilot._roliks({"vod": "youtube_x", "id": 1}, source) == [])


def broken(stream, path):
    raise RuntimeError("твич не ответил")


pilot.planner = broken
check("ролики сломались — клипы не пропадают", pilot._roliks({"vod": "2", "id": 1}, source)
      == [])

shutil.rmtree(work, ignore_errors=True)
print()
print("итого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
