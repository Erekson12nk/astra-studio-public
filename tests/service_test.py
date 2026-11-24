# -*- coding: utf-8 -*-
"""Служба: сторожа всех стримеров, работа по одному, замок между процессами.

Сторож эфира и автопилот подставные: проверяется, кто за кем следит, что делается
после конца эфира и что два процесса не режут стримы разом. Сеть не нужна.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _REPO)

import shutil
import subprocess
import tempfile
import time

work = tempfile.mkdtemp(prefix="service_test_")
_os.environ["ASTRA_PROFILES"] = _os.path.join(work, "profiles")

from core import autopilot as ap
from core import profiles as _pro
from core import service as svc
from core.lock import Lock

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-58s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


def person(name, **opts):
    profile = _pro.save({"name": name, "created": time.time(), "face": "", "keep_days": 3})
    ap.save_settings(profile, opts)
    return _pro.load(name)


print("стримеры")
person("стример", twitch="https://www.twitch.tv/somestreamer")
person("второй", twitch="other_guy")
person("без канала")
person("выключен", twitch="sleepy", watch=False)
check("стримеры — профили с каналом и слежкой",
      sorted(p["name"] for p in ap.streamers()) == ["второй", "стример"])
check("по умолчанию выкладывается в «ограниченном доступе»",
      ap.settings(_pro.load("стример"))["visibility"] == "private")
command = ap.render_command(_pro.load("второй"), "s.mp4", "tc.txt", "out", engine=["E"])
check("монтаж берёт фото своего стримера, а не активного",
      command[-2:] == ["--profile", "второй"], str(command[-3:]))

print("старые записи нового стримера")
old_vods = [{"vod": "1%d" % n, "url": "u%d" % n, "title": "старый %d" % n, "duration": 1.0,
             "started": 0.0} for n in range(3)]
fresh = ap.Pilot(_pro.load("стример"), log=lambda text: None,
                 lister=lambda c, l: list(listing))
listing = list(old_vods)
check("новый стример: старые записи пропущены", fresh.skip_old() == 3
      and not fresh.waiting_streams())
listing = [{"vod": "99", "url": "u99", "title": "новый эфир", "duration": 1.0,
            "started": 0.0}] + old_vods
check("второй раз ничего не пропускается", fresh.skip_old() == 0)
fresh.scan()
check("новый эфир после этого — в работу",
      [s["vod"] for s in fresh.waiting_streams()] == ["99"])
link = ap.connect(_pro.load("стример"))
link.execute("DELETE FROM streams")
link.commit()
link.close()
onair = ap.Pilot(_pro.load("стример"), log=lambda text: None, lister=lambda c, l: [
    {"vod": "77", "url": "u77", "title": "идёт сейчас", "duration": 1.0, "started": 0.0}]
    + old_vods, live=lambda channel: {"id": "1", "started": 5000.0},
    dates=lambda vod: 5007.0 if vod == "77" else 100.0)
check("запись идущего эфира не считается старой", onair.skip_old() == 3
      and all(s["vod"] != "77" for s in ap.streams(_pro.load("стример"))))
check("время записи без даты спрошено у твича",
      [i["started"] for i in onair.listed()][:1] == [5007.0])
link = ap.connect(_pro.load("стример"))
link.execute("DELETE FROM streams")
link.commit()
link.close()

print("замок")
path = _os.path.join(work, "x.lock")
first, second = Lock(path), Lock(path)
check("первый берёт замок", first.take())
check("второй в том же процессе — нет", not second.take() and second.busy())
first.free()
check("после освобождения — да", second.take())
second.free()
code = ("import sys; sys.path.insert(0, %r); from core.lock import Lock; "
        "l = Lock(%r); print(l.take()); sys.stdout.flush(); sys.stdin.readline()"
        % (_REPO, path))
other = subprocess.Popen([_sys.executable, "-c", code], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)
took = other.stdout.readline().strip()
check("замок держит другой процесс — здесь занято", took == "True" and Lock(path).busy())
other.stdin.write("\n")
other.stdin.flush()
other.wait(timeout=20)
check("процесс вышел — замок снят сам", not Lock(path).busy())

held = ap.processing_lock()
held.take()
logs = []
pilot = ap.Pilot(_pro.load("стример"), log=logs.append, lister=lambda c, l: [])
report = pilot.run(post=False)
check("занято — часовой запуск пропускается", report.get("busy") and
      any("другой процесс" in line for line in logs))
held.free()
check("свободно — запуск идёт", not pilot.run(post=False).get("busy"))


class FakeWatcher:
    def __init__(self, profile, channel):
        self.channel = channel
        self.finishes = 0
        self.finished = None
        self.current = None
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


made, jobs_done, notes = {}, [], []


def make_watcher(profile, channel):
    made[profile["name"]] = FakeWatcher(profile, channel)
    return made[profile["name"]]


class FakePilot:
    def __init__(self, profile):
        self.name = profile["name"]

    def after_live(self, started=0.0):
        jobs_done.append(("after_live", self.name, started))
        return {"new": 1, "queued": 4, "posted": None, "errors": []}

    def skip_old(self):
        jobs_done.append(("skip_old", self.name))
        return 0

    def run(self, post=True, wait=False):
        jobs_done.append(("run", self.name, wait))
        return {"new": 0, "queued": 0, "posted": None, "errors": []}

    def post_next(self):
        jobs_done.append(("post", self.name))
        return None


tock = [10000.0]
service = svc.Service(log=lambda text: None, notify=notes.append, watcher=make_watcher,
                      pilot=FakePilot, clock=lambda: tock[0])


def drain():
    while not service.jobs.empty():
        kind, name, data = service.jobs.get()
        service.work(kind, name, data)


print("служба")
service.refresh()
check("сторож на каждого стримера", sorted(made) == ["второй", "стример"]
      and made["стример"].channel == "somestreamer" and all(w.started for w in made.values()))
check("при запуске — проверка записей всех стримеров",
      sorted(j[1] for j in service.pending if j[0] == "run") == ["второй", "стример"])
drain()
check("проверка ждёт своей очереди, а не пропускается",
      all(j[2] is True for j in jobs_done if j[0] == "run")
      and [j[0] for j in jobs_done].count("run") == 2
      and [j[0] for j in jobs_done].count("skip_old") == 2, str(jobs_done))
jobs_done.clear()
service.refresh()
check("через минуту ничего лишнего", service.jobs.empty())
made["стример"].finished = {"channel": "somestreamer", "folder": work, "parts": 1,
                          "bytes": 1 << 20, "seconds": 3600, "started": 1794000000.0}
made["стример"].finishes = 1
service.refresh()
drain()
check("конец эфира — нарезка этого стримера по времени начала",
      jobs_done == [("after_live", "стример", 1794000000.0)], str(jobs_done))
check("уведомления: эфир кончился и клипы ждут",
      any("закончился" in n for n in notes) and any("4 клипов" in n for n in notes),
      str(notes))
jobs_done.clear()
service.refresh()
check("тот же конец эфира второй раз не режется", service.jobs.empty())
ap.save_settings(_pro.load("второй"), {"after_live": False})
made["второй"].finished = {"channel": "other_guy", "started": 1.0}
made["второй"].finishes = 1
service.refresh()
check("без галочки «сразу после эфира» — только уведомление", service.jobs.empty())
person("новенький", twitch="newbie")
old = made["второй"]
ap.save_settings(_pro.load("второй"), {"watch": False})
service.refresh()
check("новый стример подхватился сам", "новенький" in service.watchers)
check("выключенный отпущен", "второй" not in service.watchers and old.stopped)
tock[0] += svc.FALLBACK + 1
service.refresh()
check("раз в час — снова проверка записей",
      sorted(j[1] for j in service.pending if j[0] == "run") == ["новенький", "стример"])
check("одна работа не встаёт в очередь дважды", not service.put("run", "стример"))
drain()
check("статус для трея", any(line.startswith("стример:") for line in service.status()))

print("автозапуск")
start = _os.path.join(work, "startup", "astra.vbs")
check("файл автозапуска создаётся", svc.autostart(True, start) and svc.autostart_on(start))
text = open(start, "rb").read().decode("mbcs" if _os.name == "nt" else "utf-8")
check("он запускает службу без окна", "service_app.pyw" in text and ", 0, False" in text)
svc.autostart(False, start)
check("и убирается", not svc.autostart_on(start))

print("одна служба")
mine = svc.instance_lock()
check("служба не запущена", not svc.running())
mine.take()
check("запущена — вторая не стартует", svc.running() and svc.main(["--once"]) == 1)
mine.free()

shutil.rmtree(work, ignore_errors=True)
print()
print("итого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
