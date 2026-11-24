"""Служба: следит за эфирами всех стримеров и режет записи, когда эфир кончился.

Окно не нужно. Раз в минуту перечитывает профили: новый стример подхватывается сам,
выключенный отпускается. Режется по одному стриму за раз — видеокарта одна.
Раз в час (и сразу при запуске) проверяются записи всех стримеров: так ловятся
эфиры, которые прошли, пока компьютер был выключен.
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
from pathlib import Path

from . import autopilot as _ap
from . import elsewhere as _else
from . import telegram as _tg
from .lock import Lock

POLL = 60.0
TG_POLL = 120.0
FALLBACK = 3600.0
LOG_NAME = "service.log"
LOG_LIMIT = 1 << 20
AUTOSTART_NAME = "AstraStudio service.vbs"


def root() -> Path:
    from . import profiles as _pro

    return _pro.root()


def instance_lock() -> Lock:
    return Lock(root() / "service.lock")


def running() -> bool:
    """Служба уже запущена — в этом или другом процессе."""
    return instance_lock().busy()


def streams_folder(profile) -> str:
    base = _ap.settings(profile)["folder"] or str(
        Path(profile["folder"]) / _ap.FOLDER / "streams")
    os.makedirs(base, exist_ok=True)
    return base


def live_folder(profile) -> str:
    from . import live as _live

    return _live.folder_of(streams_folder(profile))


def write_log(text, folder=None):
    path = Path(folder or root()) / LOG_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > LOG_LIMIT:
            path.replace(path.with_suffix(".old.log"))
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(text).rstrip() + "\n")
    except OSError:
        pass


class Service:
    def __init__(self, log=None, notify=None, watcher=None, pilot=None, profiles=None,
                 clock=None, news=None, recorder=None, threads=True):
        self.log = log or write_log
        self.shout = notify or (lambda text: None)
        self.make_watcher = watcher or self._watcher
        self.make_pilot = pilot or self._pilot
        self.profiles = profiles or _ap.streamers
        self.clock = clock or time.time
        self.news = news or _tg.news
        self.recorder = recorder or _else.record
        self.threads = threads
        self.tg_last = {}
        self.captures = {}
        self.watchers = {}
        self.channels = {}
        self.seen = {}
        self.state = {}
        self.jobs = queue.Queue()
        self.pending = set()
        self.doing = ""
        self.last_fallback = 0.0
        self._stop = threading.Event()
        self._threads = []

    def _watcher(self, profile, channel):
        from . import live as _live

        name = profile["name"]
        return _live.Watcher(channel, live_folder(profile),
                             on_state=lambda text: self.state.__setitem__(name, text))

    def _pilot(self, profile):
        return _ap.Pilot(profile, log=lambda text: self.log("%s: %s" % (profile["name"], text)),
                         cancel=self._stop.is_set, judge=_ap.judge_for(profile))

    def notify(self, text):
        """Уведомление в трей."""
        self.shout(text)

    def put(self, kind, name, data=None) -> bool:
        if (kind, name) in self.pending:
            return False
        self.pending.add((kind, name))
        self.jobs.put((kind, name, data))
        return True

    def refresh(self):
        """Один шаг: сверить стримеров со сторожами, собрать работу."""
        from . import live as _live

        wanted = {}
        for profile in self.profiles():
            wanted[profile["name"]] = profile
        twitch = {name: _ap.channel_name(_ap.settings(p)["twitch"]) for name, p in wanted.items()}
        wanted_twitch = {name: p for name, p in wanted.items() if twitch[name]}
        for name in list(self.watchers):
            channel = twitch.get(name, "")
            if channel != self.channels.get(name):
                self.watchers.pop(name).stop()
                self.channels.pop(name, None)
                self.state.pop(name, None)
                self.log("%s: больше не слежу" % name)
        for name, profile in wanted_twitch.items():
            if name in self.watchers:
                continue
            channel = twitch[name]
            watcher = self.make_watcher(profile, channel)
            self.watchers[name] = watcher
            self.channels[name] = channel
            self.seen[name] = watcher.finishes
            watcher.start()
            self.log("%s: слежу за эфиром %s" % (name, channel))
        for name, watcher in self.watchers.items():
            if watcher.finishes == self.seen.get(name, 0):
                continue
            self.seen[name] = watcher.finishes
            done = dict(watcher.finished or {})
            self.notify(_live.finished_text(done) or "Эфир %s закончился." % name)
            if _ap.settings(wanted[name])["after_live"]:
                self.put("after_live", name, done)
        now = self.clock()
        self.check_telegram(wanted, now)
        if now - self.last_fallback >= FALLBACK:
            self.last_fallback = now
            for name in wanted:
                self.put("run", name)
            self.put("clean", "")
        for name, profile in wanted.items():
            opts = _ap.settings(profile)
            if (opts["post_while_open"] and _ap.approved(profile, _ap.CLIP)
                    and _ap.next_post_at(profile, now, _ap.CLIP) <= now):
                self.put("post", name)
            if (opts["rolik_post"] and _ap.approved(profile, _ap.ROLIK)
                    and _ap.next_post_at(profile, now, _ap.ROLIK) <= now):
                self.put("post_rolik", name)

    def check_telegram(self, wanted, now):
        for name, profile in wanted.items():
            channel = _ap.settings(profile)["telegram"]
            if not _tg.channel_of(channel) or now - self.tg_last.get(name, -TG_POLL) < TG_POLL:
                continue
            self.tg_last[name] = now
            try:
                posts = self.news(profile, channel)
            except Exception as error:
                self.log("%s: телеграм не ответил: %s" % (name, str(error).splitlines()[0][:100]))
                continue
            for post in posts:
                self.announce(name, profile, post)

    def announce(self, name, profile, post):
        """Анонс со ссылкой: свой твич — разбудить сторожа, другая площадка — писать."""
        own = _ap.channel_name(_ap.settings(profile)["twitch"])
        links = post["links"]
        if own and name in self.watchers and any(
                link["platform"] == "twitch" and link["channel"] == own for link in links):
            self.watchers[name].poke()
            self.log("%s: анонс в телеграме — проверяю твич" % name)
            return
        for link in links:
            key = (name, link["platform"], link["channel"] or link["url"])
            if key in self.captures:
                continue
            self.captures[key] = {"name": name, "platform": link["platform"],
                                  "url": link["url"], "since": self.clock()}
            self.notify("%s: анонс эфира на %s — жду начала и пишу" % (name, link["platform"]))
            args = (key, profile, link, post)
            if self.threads:
                threading.Thread(target=self._capture, args=args, daemon=True).start()
            else:
                self._capture(*args)

    def _capture(self, key, profile, link, post):
        name = profile["name"]
        base = _else.base_name(link["platform"], link["channel"], link["url"], self.clock)
        try:
            path = self.recorder(link["url"], streams_folder(profile), base,
                                 cancel=self._stop.is_set,
                                 log=lambda text: self.log("%s: %s" % (name, text)))
        except Exception as error:
            self.log("%s: запись %s сломалась: %s" % (name, link["url"], error))
            path = ""
        finally:
            self.captures.pop(key, None)
        if not path:
            return
        title = (post.get("text") or "")[:120] or base
        _ap.add_local(profile, path, title, base, link["url"])
        self.notify("%s: эфир на %s закончился — режу запись" % (name, link["platform"]))
        self.put("run", name)

    def work(self, kind, name, data=None):
        from . import profiles as _pro

        self.pending.discard((kind, name))
        if kind == "clean":
            from . import cleanup as _clean

            try:
                return _clean.sweep_all(self.profiles(), log=self.log)
            except Exception as error:
                self.log("чистка диска сломалась: %s" % error)
                return None
        try:
            profile = _pro.load(name)
        except Exception as error:
            self.log("%s: профиль не открылся: %s" % (name, error))
            return None
        pilot = self.make_pilot(profile)
        self.doing = {"after_live": "жду запись эфира и режу", "run": "проверяю записи",
                      "post": "выкладываю клип",
                      "post_rolik": "выкладываю ролик"}.get(kind, kind) + ": " + name
        try:
            if kind == "after_live":
                report = pilot.after_live(started=(data or {}).get("started") or 0.0)
            elif kind == "post_rolik":
                report = pilot.post_next(kind=_ap.ROLIK)
                if report:
                    self.notify("%s: ролик выложен" % name)
                return report
            elif kind == "run":
                if _ap.settings(profile)["skip_old"] and pilot.skip_old():
                    self.notify("%s: старые записи пропущены, режу новые эфиры" % name)
                report = pilot.run(post=False, wait=True)
            else:
                report = pilot.post_next()
                if report:
                    self.notify("%s: выложен клип в очереди" % name)
                return report
            if report and report.get("queued"):
                self.notify("%s: %d клипов ждут одобрения" % (name, report["queued"]))
            for error in (report or {}).get("errors") or ():
                self.notify("⚠ %s: %s" % (name, str(error)[:150]))
            return report
        except Exception as error:
            self.log("%s: %s сломалось: %s" % (name, kind, error))
            self.notify("⚠ %s: ошибка — %s" % (name, str(error).splitlines()[0][:150]))
            return None
        finally:
            self.doing = ""

    def status(self) -> list:
        lines = []
        for name in sorted(self.watchers):
            lines.append("%s: %s" % (name, self.state.get(name) or "проверяю эфир…"))
        for item in list(self.captures.values()):
            lines.append("%s: пишу эфир %s" % (item["name"], item["platform"]))
        if self.doing:
            lines.append("сейчас: " + self.doing)
        waiting = self.jobs.qsize()
        if waiting:
            lines.append("в очереди работ: %d" % waiting)
        return lines or ["нет стримеров со вписанным твичем или телеграмом"]

    def recording(self) -> bool:
        return bool(self.captures) or any(watcher.current is not None
                                          for watcher in self.watchers.values())

    def _loop_refresh(self):
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as error:
                self.log("служба споткнулась: %s" % error)
            self._stop.wait(POLL)

    def _loop_work(self):
        while not self._stop.is_set():
            try:
                kind, name, data = self.jobs.get(timeout=1.0)
            except queue.Empty:
                continue
            self.work(kind, name, data)

    def start(self):
        self._stop.clear()
        for target in (self._loop_refresh, self._loop_work):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self._threads.append(thread)
        self.log("служба запущена")

    def stop(self):
        self._stop.set()
        for watcher in self.watchers.values():
            watcher.stop()
        self.watchers.clear()
        self.log("служба остановлена")


def pythonw() -> str:
    folder = os.path.dirname(sys.executable)
    windowed = os.path.join(folder, "pythonw.exe")
    return windowed if os.path.isfile(windowed) else sys.executable


def repo() -> str:
    return str(Path(__file__).resolve().parent.parent)


def start_command() -> list:
    return [pythonw(), os.path.join(repo(), "studio", "service_app.pyw")]


def launch():
    """Запустить службу отдельным процессом без окна консоли."""
    import subprocess

    from . import ffmpeg as _ff

    return subprocess.Popen(start_command(), cwd=repo(), **_ff._NOWIN)


def autostart_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" \
        / AUTOSTART_NAME


def autostart_text() -> str:
    command = " ".join('""%s""' % part for part in start_command())
    return "\r\n".join((
        'Set shell = CreateObject("WScript.Shell")',
        'shell.CurrentDirectory = "%s"' % repo(),
        'shell.Run "%s", 0, False' % command,
        ""))


def autostart(on=True, path=None) -> bool:
    path = Path(path or autostart_path())
    if not on:
        path.unlink(missing_ok=True)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(autostart_text().encode("mbcs" if os.name == "nt" else "utf-8",
                                             errors="replace"))
    return True


def autostart_on(path=None) -> bool:
    return Path(path or autostart_path()).is_file()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="service")
    parser.add_argument("--once", action="store_true", help="один шаг и выход")
    args = parser.parse_args(argv)
    lock = instance_lock()
    if not lock.take():
        print("служба уже запущена")
        return 1

    def say(text):
        print(text, flush=True)
        write_log(text)

    service = Service(log=say, notify=say)
    if args.once:
        service.refresh()
        for line in service.status():
            print(line)
        service.stop()
        return 0
    service.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
