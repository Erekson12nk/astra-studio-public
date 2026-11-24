# -*- coding: utf-8 -*-
"""Полуавтопилот: новый стрим → клипы → очередь на одобрение → выгрузка по одному.

Твич, поиск, проверка и выгрузка подставные, база очереди настоящая. Монтаж
проверяется отдельно: подставной движок печатает строки «auto name:», как
настоящий, и автопилот по ним находит готовые файлы. В конце строится настоящая
Студия и вкладка «Автопилот»: настройки сохраняются в профиль, очередь видна,
одобрение из окна доходит до базы.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import json
import shutil
import tempfile
import time

work = tempfile.mkdtemp(prefix="autopilot_test_")
_os.environ["ASTRA_PROFILES"] = _os.path.join(work, "profiles")
_os.environ["ASTRA_STUDIO_SETTINGS"] = _os.path.join(work, "settings.json")

import tkinter.messagebox as _mb

DIALOGS = []
_mb.showerror = lambda *a, **k: DIALOGS.append(a)
_mb.showinfo = lambda *a, **k: DIALOGS.append(a)
_mb.askyesno = lambda *a, **k: False

from core import autopilot as ap
from core import profiles as _pro

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


_pro.save({"name": "стример", "created": time.time(), "face": "", "keep_days": 3})
_pro.set_active("стример")
profile = _pro.load("стример")
streams_dir = _os.path.join(work, "стримы")
_os.makedirs(streams_dir)
local = _os.path.join(streams_dir, "все еще больной [v2905817641].mp4")
with open(local, "wb") as handle:
    handle.write(b"0")

print("откуда")
check("канал по имени", ap.channel_url("somestreamer")
      == "https://www.twitch.tv/somestreamer/videos?filter=archives&sort=time")
check("канал по ссылке", ap.channel_url("https://www.twitch.tv/somestreamer").endswith(
    "/videos?filter=archives&sort=time"))
check("номер записи из ссылки и из имени файла",
      ap.vod_of("https://www.twitch.tv/videos/2905817641") == "2905817641"
      and ap.vod_of(_os.path.basename(local)) == "2905817641")
listing = json.dumps({"entries": [
    {"id": "v2905817641", "url": "https://www.twitch.tv/videos/2905817641",
     "title": "все еще больной", "duration": 22110},
    {"id": "v2904972027", "url": "https://www.twitch.tv/videos/2904972027",
     "title": "вчерашний", "duration": 12850}]}, ensure_ascii=False)
asked = []
vods = ap.list_vods("somestreamer", 5, runner=lambda args: asked.append(args) or listing)
check("список записей разбирается", [v["vod"] for v in vods] == ["2905817641", "2904972027"])
check("yt-dlp спрашивается без скачивания", "--flat-playlist" in asked[0])
check("скачанная запись находится в папке", ap.find_local(streams_dir, "2905817641") == local)

ap.save_settings(profile, {"twitch": "somestreamer", "folder": streams_dir, "per_stream": 3,
                           "gap_hours": 6.0, "title": "", "donations": True, "away": False})
profile = _pro.load("стример")
fetched, rendered, posted = [], [], []


def finder(path):
    return [{"start": 60.0 * n, "end": 60.0 * n + 30, "score": 9 - n, "title": "момент %d" % n}
            for n in range(1, 4)]


def checker(path, moments):
    kept = [dict(moments[0], drops=[(70.0, 75.0, "донат не по теме")]), moments[2]]
    return kept


def renderer(stream, path, moments):
    files = []
    for number, item in enumerate(moments, 1):
        target = _os.path.join(work, "клип_%s_%d.mp4" % (stream["vod"], number))
        with open(target, "wb") as handle:
            handle.write(b"0")
        files.append(target)
    rendered.append((path, len(moments)))
    return files


def fetcher(stream):
    fetched.append(stream["vod"])
    target = _os.path.join(streams_dir, "%s [v%s].mp4" % (stream["title"], stream["vod"]))
    with open(target, "wb") as handle:
        handle.write(b"0")
    return target


def uploader(item, title):
    posted.append((item["id"], title))
    return {"url": "https://youtu.be/x%d" % item["id"]}


logs = []
pilot = ap.Pilot(profile, log=logs.append, lister=lambda channel, limit: vods,
                 fetcher=fetcher, finder=finder, checker=checker, renderer=renderer,
                 uploader=uploader)

print("нарезка")
report = pilot.run(post=False)
check("новые записи заведены", report["new"] == 2)
check("за раз обработан один стрим", len(rendered) == 1)
check("уже скачанная запись не качалась заново", fetched == [] and rendered[0][0] == local)
check("в очередь легли клипы после проверки (3 → 2)", report["queued"] == 2)
waiting = ap.queue(profile, (ap.READY,))
check("клипы ждут одобрения", len(waiting) == 2 and all(r["status"] == ap.READY for r in waiting))
check("видно, сколько вырезов внутри", waiting[0]["cuts"] == 1)
again = pilot.run(post=False)
check("второй прогон берёт следующий стрим и качает его", again["new"] == 0
      and fetched == ["2904972027"] and len(rendered) == 2)
check("повторно ничего не заводится", len(ap.streams(profile)) == 2)

print("выгрузка")
check("без одобрения ничего не выкладывается", pilot.post_next() is None and posted == [])
ids = [row["id"] for row in ap.queue(profile, (ap.READY,))]
check("одобрение", ap.decide(profile, ids[:2], ap.APPROVED) == 2)
ap.decide(profile, ids[2:3], ap.REJECTED)
first = pilot.post_next(now=time.time())
check("выложен первый одобренный", first == ids[0] and posted[0][1] == "момент 1")
check("второй сразу не выкладывается — выдержка", pilot.post_next(now=time.time()) is None)
check("через 6 часов — можно", pilot.post_next(now=time.time() + 6.1 * 3600) == ids[1])
check("отклонённый не выкладывается",
      all(item_id != ids[2] for item_id, _t in posted))
check("статусы в базе", [r["status"] for r in ap.queue(profile)][:3]
      == [ap.POSTED, ap.POSTED, ap.REJECTED])


def broken(item, title):
    raise RuntimeError("Google просит подтвердить личность")


ap.decide(profile, ids[3:4], ap.APPROVED)
bad = ap.Pilot(profile, log=logs.append, uploader=broken)
try:
    bad.post_next(force=True)
    check("сбой выгрузки не теряется", False)
except RuntimeError:
    row = [r for r in ap.queue(profile) if r["id"] == ids[3]][0]
    check("сбой выгрузки не теряется", row["status"] == ap.FAILED and "личность" in row["error"])
check("неудачный можно одобрить снова", ap.decide(profile, ids[3:4], ap.APPROVED) == 1)

print("монтаж настоящим путём")
fake_engine = _os.path.join(work, "движок.py")
with open(fake_engine, "w", encoding="utf-8") as handle:
    handle.write(
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "folder = args[args.index('--out') + 1]\n"
        "lines = open(args[args.index('--timecodes') + 1], encoding='utf-8').read()\n"
        "count = sum(1 for line in lines.splitlines() if line and not line.startswith('#'))\n"
        "for n in range(1, count + 1):\n"
        "    name = '%02d_00-01-00_клип.mp4' % n\n"
        "    open(os.path.join(folder, name), 'wb').write(b'0')\n"
        "    print('=== clip %d/%d ===' % (n, count))\n"
        "    print('      auto name: ' + name, flush=True)\n")
ap.engine_command = lambda: [_sys.executable, fake_engine]
files = ap.Pilot(profile, log=logs.append)._render(
    {"vod": "1"}, local, [{"start": 10, "end": 40, "title": "a",
                          "drops": [(15.0, 20.0, "донат")]},
                         {"start": 100, "end": 130, "title": "b"}])
check("файлы клипов найдены по выводу движка",
      len(files) == 2 and all(_os.path.isfile(f) for f in files))
tc = open(_os.path.join(_os.path.dirname(files[0]), "_моменты.txt"), encoding="utf-8").read()
check("вырезы доната ушли в движок через таймкоды", "# вырезать" in tc)
command = ap.render_command(profile, local, "tc.txt", "out", engine=["E"])
check("движок зовётся с таймкодами, папкой и раскладкой",
      "--timecodes" in command and "--out" in command and "auto" in command)

print("сам по расписанию")
bat = ap.launcher(work)
check("запускной файл только ASCII", open(bat, "rb").read().decode("ascii").startswith(
    "@echo off"))
check("задача Windows раз в час", ap.task_on_command(bat)[:7]
      == ["schtasks", "/Create", "/F", "/SC", "HOURLY", "/MO", "1"])

print("сразу после эфира")
ap.save_settings(profile, {"twitch": "https://www.twitch.tv/somestreamer"})
profile = _pro.load("стример")
check("автопилот следит за каналом из ссылки", ap.follows(profile, "somestreamer")
      and ap.follows(profile, "SomeStreamer") and not ap.follows(profile, "другой"))
ap.save_settings(profile, {"after_live": False})
check("галочка выключает запуск после эфира", not ap.follows(_pro.load("стример"), "somestreamer"))
ap.save_settings(profile, {"after_live": True, "donations": False, "away": False})
profile = _pro.load("стример")
AIR = 1794000000.0
on_air = [{"id": "5", "started": AIR}]
listed = [[]]
naps = []
tonight = {"vod": "2910000001", "url": "https://www.twitch.tv/videos/2910000001",
           "title": "ночной", "duration": 0.0, "started": AIR + 6}
live_logs = []
night = ap.Pilot(profile, log=live_logs.append, lister=lambda channel, limit: list(listed[0]),
                 fetcher=fetcher, finder=finder, checker=checker, renderer=renderer,
                 uploader=uploader, live=lambda channel: on_air[0], sleep=naps.append)
listed[0] = [tonight] + vods
before = len(rendered)
night.scan()
check("запись идущего эфира не берётся в работу",
      all(s["vod"] != tonight["vod"] for s in ap.streams(profile))
      and any("эфир ещё идёт" in line for line in live_logs))
on_air[0] = None
listed[0] = list(vods)
report = night.after_live(started=AIR, tries=3, wait=180.0)
check("нет записи эфира — ждёт и сдаётся без нарезки", report is None
      and naps == [60.0, 180.0, 180.0] and len(rendered) == before, str(naps))
calls = [0]


def appears(channel, limit):
    calls[0] += 1
    return ([tonight] if calls[0] >= 2 else []) + list(vods)


naps[:] = []
night.lister = appears
report = night.after_live(started=AIR, tries=5, wait=180.0)
check("запись появилась — сразу нарезана в очередь",
      report and report["queued"] >= 1 and naps == [60.0, 180.0]
      and rendered[-1][0].endswith("[v2910000001].mp4"), "%s %s" % (report, naps))
check("после эфира ничего не выкладывается без одобрения", report and report["posted"] is None)
stopped = ap.Pilot(profile, lister=appears, live=lambda channel: None, sleep=naps.append,
                   cancel=lambda: True)
check("остановка прерывает ожидание", stopped.after_live(started=AIR) is None)
ap.save_settings(profile, {"twitch": "somestreamer"})
profile = _pro.load("стример")

print("вкладка")
ap.decide(profile, ids[2:3], ap.READY)
from studio.window import Studio

app = Studio()
app.update()
app._switch("clip")
app._switch_clip("pilot")
app.update()
panel = app.pilot
check("вкладка «Автопилот» строится", panel is not None and panel.winfo_ismapped())
check("настройки профиля подставлены", panel.vars["twitch"].get() == "somestreamer"
      and panel.vars["per_stream"].get() == "3")
panel.vars["gap_hours"].set("8")
panel.save()
check("сохранение пишет в профиль", ap.settings(_pro.load("стример"))["gap_hours"] == 8.0)
panel.vars["max_len"].set("5")
check("кривая длина не сохраняется", panel.save() is None and DIALOGS)
panel.vars["max_len"].set("60")
panel.refresh()
shown = len(panel.tree.get_children())
check("в очереди видны ждущие и одобренные", shown >= 2)
waiting = [int(i) for i in panel.tree.get_children()
           if panel.tree.set(i, "status") == ap.READY]
panel.tree.selection_set([str(waiting[0])])
panel._decide(ap.APPROVED)
check("одобрение из окна доходит до базы",
      [r for r in ap.queue(_pro.load("стример")) if r["id"] == waiting[0]][0]["status"]
      == ap.APPROVED)
check("видно, когда следующая выгрузка", "следующая выгрузка" in panel.lbl_state.cget("text"))
app.destroy()

shutil.rmtree(work, ignore_errors=True)
print()
print("итого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
