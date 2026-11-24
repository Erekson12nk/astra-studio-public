from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from . import ffmpeg as _ff
from . import handoff as _hand
from .lock import Lock

DB_NAME = "autopilot.db"
FOLDER = "autopilot"
NEW = "новый"
WORKING = "в работе"
DONE = "готов"
BROKEN = "ошибка"
SKIPPED = "пропущен"
CLIP = "клип"
ROLIK = "ролик"
READY = "ждёт"
APPROVED = "одобрен"
REJECTED = "отклонён"
POSTED = "выложен"
FAILED = "не выложен"
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov")
VOD = re.compile(r"(?:videos/|\[v|^v)(\d{6,14})")
AUTO_NAME = re.compile(r"auto name:\s*(.+?)\s*$")
AFTER_LIVE_TRIES = 20
AFTER_LIVE_WAIT = 180.0
VOD_SLACK = 900.0
DEFAULTS = {
    "twitch": "",
    "folder": "",
    "per_stream": 8,
    "streams_per_run": 1,
    "look_back": 5,
    "min_len": 20.0,
    "max_len": 60.0,
    "sense": False,
    "donations": True,
    "away": True,
    "gap_hours": 6.0,
    "title": "",
    "service": "youtube",
    "account": "main",
    "channel": "",
    "visibility": "private",
    "post_while_open": False,
    "after_live": True,
    "watch": True,
    "skip_old": True,
    "telegram": "",
    "roliks": True,
    "rolik_min": 15.0,
    "rolik_title": "{стример} играет в {игра}",
    "rolik_donations": True,
    "rolik_away": True,
    "reactions": True,
    "reaction_min": 10.0,
    "reaction_title": "{стример} смотрит: {видео}",
    "screen_titles": True,
    "rolik_post": True,
    "rolik_gap_hours": 24.0,
    "rolik_visibility": "private",
    "keep_source_days": 2.0,
    "max_source_days": 7.0,
    "keep_live_days": 7.0,
    "keep_rejected_days": 2.0,
    "keep_posted_days": 7.0,
    "min_free_gb": 60.0,
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS streams (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vod TEXT UNIQUE, url TEXT, title TEXT,
    path TEXT, status TEXT, error TEXT, found_at REAL, done_at REAL);
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT, stream_id INTEGER, file TEXT, title TEXT,
    start REAL, finish REAL, score REAL, cuts INTEGER DEFAULT 0, status TEXT,
    error TEXT, made_at REAL, decided_at REAL, posted_at REAL, url TEXT);
"""


def settings(profile) -> dict:
    merged = dict(DEFAULTS)
    merged.update((profile or {}).get("autopilot") or {})
    return merged


def save_settings(profile, data) -> dict:
    from . import profiles as _pro

    kept = dict(settings(profile))
    kept.update({key: value for key, value in data.items() if key in DEFAULTS})
    profile["autopilot"] = kept
    _pro.save(profile)
    return kept


def vod_of(text) -> str:
    found = VOD.search(str(text or ""))
    return found.group(1) if found else ""


def channel_url(text) -> str:
    text = str(text or "").strip().rstrip("/")
    if not text:
        return ""
    if text.startswith("http"):
        if "/videos" in text:
            return text
        return text + "/videos?filter=archives&sort=time"
    return "https://www.twitch.tv/%s/videos?filter=archives&sort=time" % text.lstrip("@")


def list_vods(channel, limit=5, runner=None) -> list:
    url = channel_url(channel)
    if not url:
        raise ValueError("не указан канал твича")
    command = [_ff.find("yt-dlp"), "--flat-playlist", "-J", "--playlist-end", str(int(limit)),
               url]
    run = runner or (lambda args: subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"), **_ff._NOWIN).stdout)
    raw = run(command)
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        raise RuntimeError("твич не отдал список записей: %s" % (raw or "")[:200])
    out = []
    for entry in data.get("entries") or ():
        vod = vod_of(entry.get("url") or entry.get("id"))
        if vod:
            out.append({"vod": vod, "url": entry.get("url") or
                        "https://www.twitch.tv/videos/%s" % vod,
                        "title": str(entry.get("title") or "").strip(),
                        "duration": float(entry.get("duration") or 0.0),
                        "started": started_of(entry)})
    return out


def channel_name(text) -> str:
    text = str(text or "").strip().rstrip("/").lstrip("@")
    found = re.search(r"twitch\.tv/([A-Za-z0-9_]{2,25})", text)
    if found:
        return found.group(1).lower()
    return text.lower() if re.fullmatch(r"[A-Za-z0-9_]{2,25}", text) else ""


def follows(profile, channel) -> bool:
    opts = settings(profile)
    name = channel_name(opts["twitch"])
    return bool(opts["after_live"] and name and name == channel_name(channel))


def trim_overlaps(plan) -> list:
    """Игра и реакция не делят секунды: граница игры уточнена по тишине, ей и верим."""
    plan = sorted((dict(item) for item in plan), key=lambda item: item["start"])
    for left, right in zip(plan, plan[1:]):
        if left["end"] > right["start"]:
            if left.get("kind") == "game":
                right["start"] = left["end"]
            else:
                left["end"] = right["start"]
    return [item for item in plan if item["end"] - item["start"] > 1.0]


def processing_lock() -> Lock:
    from . import profiles as _pro

    return Lock(_pro.root() / "processing.lock")


def rename(profile, queue_id, title) -> bool:
    """Новое название клипа в очереди."""
    link = connect(profile)
    try:
        changed = link.execute("UPDATE queue SET title = ? WHERE id = ?",
                               (" ".join(str(title).split())[:200], int(queue_id))).rowcount
        link.commit()
    finally:
        link.close()
    return bool(changed)


_STARTED = re.compile(r"_(\d{9,11})/+thumb/")


def started_of(raw) -> float:
    """Время начала записи твича по ссылке на её превью."""
    found = _STARTED.search(str((raw or {}).get("thumbnail") or ""))
    return float(found.group(1)) if found else 0.0


def streamers() -> list:
    """Профили, за чьими эфирами следит служба: канал твича вписан, слежка включена."""
    from . import profiles as _pro

    out = []
    for name in _pro.names():
        try:
            profile = _pro.load(name)
        except Exception:
            continue
        opts = settings(profile)
        if opts["watch"] and (channel_name(opts["twitch"]) or telegram_name(opts["telegram"])):
            out.append(profile)
    return out


def telegram_name(text) -> str:
    from . import telegram as _tg

    return _tg.channel_of(text)


def add_local(profile, path, title, key, url="") -> int:
    """Эфир, записанный самим (не с твича), — в очередь на нарезку как обычная запись."""
    link = connect(profile)
    try:
        cursor = link.execute("INSERT OR IGNORE INTO streams (vod, url, title, path, status,"
                              " found_at, ours) VALUES (?, ?, ?, ?, ?, ?, 1)",
                              (key, url, title, str(path), NEW, time.time()))
        link.commit()
        return cursor.lastrowid or 0
    finally:
        link.close()


def is_this_stream(item, started) -> bool:
    return bool(started and item.get("started")
                and abs(float(item["started"]) - float(started)) <= VOD_SLACK)


def vod_started(vod) -> float:
    from . import live as _live

    try:
        info = _live.vod_info(vod)
    except Exception:
        return 0.0
    return float(info["started"]) if info else 0.0


def live_stream(channel):
    from . import live as _live

    try:
        return _live.live_now(channel_name(channel))
    except Exception:
        return None


def find_local(folder, vod) -> str:
    if not folder or not os.path.isdir(str(folder)):
        return ""
    for name in os.listdir(str(folder)):
        if name.lower().endswith(VIDEO_EXT) and vod_of(name) == vod:
            return os.path.join(str(folder), name)
    return ""


def db_path(profile) -> Path:
    return Path(profile["folder"]) / DB_NAME


def connect(profile):
    link = sqlite3.connect(str(db_path(profile)))
    link.row_factory = sqlite3.Row
    link.executescript(SCHEMA)
    for table, column in (("queue", "kind TEXT DEFAULT '%s'" % CLIP),
                          ("streams", "ours INTEGER DEFAULT 0"),
                          ("streams", "removed_at REAL")):
        try:
            link.execute("ALTER TABLE %s ADD COLUMN %s" % (table, column))
        except sqlite3.OperationalError:
            pass
    return link


def queue(profile, statuses=None) -> list:
    link = connect(profile)
    try:
        query = ("SELECT q.*, s.title AS stream FROM queue q LEFT JOIN streams s"
                 " ON s.id = q.stream_id")
        args = ()
        if statuses:
            query += " WHERE q.status IN (%s)" % ",".join("?" * len(statuses))
            args = tuple(statuses)
        rows = link.execute(query + " ORDER BY q.id", args).fetchall()
    finally:
        link.close()
    return [dict(row) for row in rows]


def streams(profile) -> list:
    link = connect(profile)
    try:
        rows = link.execute("SELECT * FROM streams ORDER BY id DESC").fetchall()
    finally:
        link.close()
    return [dict(row) for row in rows]


def decide(profile, ids, status) -> int:
    if status not in (APPROVED, REJECTED, READY):
        raise ValueError("нельзя поставить такой статус: %s" % status)
    link = connect(profile)
    try:
        changed = 0
        for item in ids:
            changed += link.execute(
                "UPDATE queue SET status = ?, decided_at = ? WHERE id = ? AND status IN"
                " (?, ?, ?, ?)", (status, time.time(), int(item), READY, APPROVED,
                                  REJECTED, FAILED)).rowcount
        link.commit()
    finally:
        link.close()
    return changed


def last_post(profile, kind=CLIP) -> float:
    link = connect(profile)
    try:
        row = link.execute("SELECT MAX(posted_at) FROM queue WHERE status = ? AND"
                           " COALESCE(kind, ?) = ?", (POSTED, CLIP, kind)).fetchone()
    finally:
        link.close()
    return float(row[0] or 0.0)


def next_post_at(profile, now=None, kind=CLIP) -> float:
    opts = settings(profile)
    gap = float(opts["rolik_gap_hours"] if kind == ROLIK else opts["gap_hours"]) * 3600.0
    last = last_post(profile, kind)
    return last + gap if last else (time.time() if now is None else now)


def approved(profile, kind=CLIP) -> list:
    return [item for item in queue(profile, (APPROVED,)) if (item.get("kind") or CLIP) == kind]


def free_gb(path) -> float:
    import shutil

    probe = Path(str(path))
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(str(probe)).free / float(1 << 30)
    except OSError:
        return float("inf")


class DiskFull(RuntimeError):
    """Места на диске под запись стрима нет — запись подождёт следующего запуска."""


def engine_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--engine"]
    root = Path(__file__).resolve().parent.parent
    return [sys.executable, str(root / "astra" / "auto_vertical_editor.py")]


def render_command(profile, source, timecodes, folder, engine=None):
    from .settings import Render

    return Render(source=str(source), out=str(folder), timecodes=str(timecodes),
                  layout=str(profile.get("layout") or "auto"),
                  model=profile.get("whisper") or None,
                  language=profile.get("language") or None).command(
                      engine or engine_command()) + ["--profile", str(profile["name"])]


class Pilot:
    def __init__(self, profile, log=None, progress=None, cancel=None, judge=None,
                 lister=None, fetcher=None, finder=None, checker=None, renderer=None,
                 uploader=None, live=None, sleep=None, dates=None, planner=None,
                 maker=None):
        self.profile = profile
        self.opts = settings(profile)
        self.log = log or (lambda text: None)
        self.progress = progress or (lambda share, note="": None)
        self.cancel = cancel or (lambda: False)
        self.judge = judge
        self.lister = lister or list_vods
        self.fetcher = fetcher or self._fetch
        self.finder = finder or self._find
        self.checker = checker or self._check
        self.renderer = renderer or self._render
        self.uploader = uploader or self._upload
        self.live = live if live is not None else (None if lister else live_stream)
        self.dates = dates if dates is not None else (None if lister else vod_started)
        self.planner = planner or (None if lister else self._plan_roliks)
        self.maker = maker or self._make_roliks
        self.sleep = sleep or self._nap

    def listed(self) -> list:
        """Записи канала; у записи идущего эфира твич не даёт времени — спрашиваем отдельно."""
        found = self.lister(self.opts["twitch"], self.opts["look_back"])
        if self.dates:
            for item in found:
                if not item.get("started"):
                    item["started"] = self.dates(item["vod"])
        return found

    def _nap(self, seconds):
        until = time.time() + seconds
        while not self.cancel() and time.time() < until:
            time.sleep(min(1.0, max(0.0, until - time.time())))

    def scan(self) -> list:
        found = self.listed()
        on_air = self.live(self.opts["twitch"]) if self.live else None
        if on_air:
            going = [item for item in found if is_this_stream(item, on_air.get("started"))]
            if going:
                self.log("эфир ещё идёт — его запись возьму после конца")
            found = [item for item in found if item not in going]
        link = connect(self.profile)
        fresh = []
        try:
            for item in found:
                if link.execute("SELECT 1 FROM streams WHERE vod = ?", (item["vod"],)).fetchone():
                    continue
                link.execute("INSERT INTO streams (vod, url, title, status, found_at)"
                             " VALUES (?, ?, ?, ?, ?)",
                             (item["vod"], item["url"], item["title"], NEW, time.time()))
                fresh.append(item)
            link.commit()
        finally:
            link.close()
        self.log("новых записей на канале: %d" % len(fresh))
        return fresh

    def skip_old(self) -> int:
        """Новый стример: записи, что были до него, не режутся — только новые эфиры."""
        if streams(self.profile) or not channel_name(self.opts["twitch"]):
            return 0
        found = self.listed()
        on_air = self.live(self.opts["twitch"]) if self.live else None
        if on_air:
            found = [item for item in found if not is_this_stream(item, on_air.get("started"))]
        link = connect(self.profile)
        try:
            for item in found:
                link.execute("INSERT OR IGNORE INTO streams (vod, url, title, status, found_at)"
                             " VALUES (?, ?, ?, ?, ?)",
                             (item["vod"], item["url"], item["title"], SKIPPED, time.time()))
            link.commit()
        finally:
            link.close()
        if found:
            self.log("старые записи пропущены: %d — режу эфиры с этого момента" % len(found))
        return len(found)

    def waiting_streams(self) -> list:
        link = connect(self.profile)
        try:
            rows = link.execute("SELECT * FROM streams WHERE status IN (?, ?) ORDER BY id",
                                (NEW, WORKING)).fetchall()
        finally:
            link.close()
        return [dict(row) for row in rows]

    def _mark(self, stream_id, **fields):
        link = connect(self.profile)
        try:
            keys = ", ".join("%s = ?" % key for key in fields)
            link.execute("UPDATE streams SET %s WHERE id = ?" % keys,
                         tuple(fields.values()) + (stream_id,))
            link.commit()
        finally:
            link.close()

    def streams_folder(self) -> str:
        return self.opts["folder"] or str(Path(self.profile["folder"]) / FOLDER / "streams")

    def room(self):
        """Перед скачиванием: места мало — почистить старое; всё равно мало — ждать."""
        need = float(self.opts["min_free_gb"])
        folder = self.streams_folder()
        if free_gb(folder) >= need:
            return
        from . import cleanup as _clean

        _clean.sweep(self.profile, log=self.log)
        left = free_gb(folder)
        if left < need:
            raise DiskFull("на диске %.0f ГБ свободно, а нужно хотя бы %.0f — запись стрима "
                           "подождёт, освободите место" % (left, need))

    def process(self, stream) -> list:
        path = stream.get("path") or find_local(self.opts["folder"], stream["vod"])
        if not path:
            self.room()
        self._mark(stream["id"], status=WORKING, error="")
        try:
            if not path:
                self.log("качаю запись: %s" % (stream.get("title") or stream["url"]))
                path = self.fetcher(stream)
                self._mark(stream["id"], ours=1)
            self._mark(stream["id"], path=path)
            self.log("ищу моменты: %s" % os.path.basename(path))
            moments = self.finder(path)
            if self.opts["donations"] or self.opts["away"]:
                moments = self.checker(path, moments)
            made = []
            if moments:
                self.log("монтирую %d клипов" % len(moments))
                files = self.renderer(stream, path, moments)
                made = self._enqueue(stream, moments, files)
            else:
                self.log("клипов в записи нет")
            made += self._roliks(stream, path)
            self._mark(stream["id"], status=DONE, done_at=time.time())
            self.log("в очередь на одобрение: %d" % len(made))
            return made
        except Exception as error:
            self._mark(stream["id"], status=BROKEN, error=str(error)[:500])
            raise

    def _roliks(self, stream, path) -> list:
        """Ролики по играм: только у записей твича — у них есть главы."""
        if (not self.planner or not self.opts["roliks"]
                or not str(stream.get("vod") or "").isdigit()):
            return []
        try:
            plan = self.planner(stream, path)
            if not plan:
                self.log("игр на стриме нет — роликов по играм нет")
                return []
            self.log("игры на стриме: %s" % ", ".join(
                "%s (%s)" % (item["game"], _ff.format_hms(item["end"] - item["start"]))
                for item in plan))
            if self.opts["rolik_donations"] or self.opts["rolik_away"]:
                plan = self.checker(path, plan, donations=self.opts["rolik_donations"],
                                    away=self.opts["rolik_away"])
            files = self.maker(stream, path, plan)
            return self._enqueue(stream, plan, files, kind=ROLIK)
        except Exception as error:
            self.log("ролики не собрались: %s" % str(error).splitlines()[0][:200])
            return []

    def _plan_roliks(self, stream, path) -> list:
        from . import roliks as _rol

        plan = _rol.plan_games(stream["vod"], self.profile, source=path,
                               min_len=float(self.opts["rolik_min"]) * 60.0,
                               template=self.opts["rolik_title"])
        if self.opts["reactions"]:
            try:
                plan += self._plan_reactions(stream, path)
            except Exception as error:
                self.log("реакции не размечены: %s" % str(error).splitlines()[0][:200])
        return trim_overlaps(plan)

    def _plan_reactions(self, stream, path) -> list:
        """Разговорные главы → реакции: где стример смотрит какое видео и как оно зовётся."""
        from . import reactions as _rx
        from . import roliks as _rol
        from . import voiceid as _voice

        found = _rol.chapters(stream["vod"])
        if not _rx.chatting(found):
            return []
        self.log("размечаю реакции: расшифровка, голоса, кадры")
        lines = self._transcript(path)
        voice = _voice.track(path) if _voice.available() else None
        look = _rx.keyframes(path)
        backend, cfg = self._backend()
        found = _rx.find(path, found, lines=lines, voice=voice, look=look,
                         box=self._webcam_box(path),
                         judge=_rx.judge_with(backend, cfg) if backend else None,
                         switch=_rx.switch_with(backend, cfg) if backend else None,
                         streamer=_rol.streamer_name(self.profile),
                         template=self.opts["reaction_title"],
                         min_len=float(self.opts["reaction_min"]) * 60.0,
                         screen=_rx.screen_with(log=self.log) if self.opts["screen_titles"]
                         else None, log=self.log)
        for item in found:
            self.log("  реакция %s–%s: %s" % (_ff.format_hms(item["start"]),
                                             _ff.format_hms(item["end"]), item["title"]))
        return found

    def _transcript(self, path) -> list:
        from . import asr as _asr

        model = self.profile.get("whisper") or "large-v3-turbo"
        language = self.profile.get("language") or "ru"
        key = _asr.cache_key(path, model, language)
        cached = _asr.load_cache(path, key)
        if cached is None:
            self.log("расшифровываю стрим (%s) — это надолго" % model)
            engine = _asr.load_model(model)
            cached = _asr.transcribe(str(path), engine, language=language,
                                     progress=lambda share, note="": self.progress(
                                         0.5 * share, "расшифровка"), cancel=self.cancel)
            _asr.save_cache(path, key, cached)
            del engine
        return [(item.start, item.end, item.text) for item in cached.segments]

    def _backend(self):
        try:
            from clipper.backends import get_backend

            cfg = studio_config(self.profile)
            return get_backend(cfg), cfg
        except Exception as error:
            self.log("модель недоступна (%s): реакции без названий" %
                     str(error).splitlines()[0][:120])
            return None, None

    def _webcam_box(self, path):
        from . import away as _away
        from . import profiles as _pro

        photo = _pro.face_path(self.profile)
        if not photo:
            return None
        try:
            return _away.locate_box(path, str(photo))
        except Exception:
            return None

    def _make_roliks(self, stream, path, plan) -> list:
        from . import live as _live
        from . import roliks as _rol

        folder = Path(self.profile["folder"]) / FOLDER / ("%s_%s" % (
            time.strftime("%Y%m%d"), stream["vod"])) / "ролики"
        base = self.opts["folder"] or str(Path(self.profile["folder"]) / FOLDER / "streams")
        try:
            patches = _live.patches_for(path, _live.folder_of(base), log=self.log) or []
        except Exception as error:
            self.log("запись эфира не подошла: %s" % str(error).splitlines()[0][:120])
            patches = []
        made = _rol.make(path, plan, str(folder), _rol.options_from_studio(), log=self.log,
                         progress=lambda share: self.progress(0.5 + 0.5 * share, "ролики"),
                         cancel=self.cancel, live=patches)
        by_title = {item["moment"].get("title"): item.get("clean") or item["video"]
                    for item in made}
        return [by_title.get(item.get("title")) for item in plan]

    def _enqueue(self, stream, moments, files, kind=CLIP) -> list:
        link = connect(self.profile)
        made = []
        try:
            for item, file in zip(moments, files):
                if not file or not os.path.isfile(file):
                    continue
                cursor = link.execute(
                    "INSERT INTO queue (stream_id, file, title, start, finish, score, cuts,"
                    " status, made_at, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (stream["id"], file, str(item.get("title") or "").strip(),
                     float(item["start"]), float(item["end"]), item.get("score"),
                     len(item.get("drops") or ()), READY, time.time(), kind))
                made.append(cursor.lastrowid)
            link.commit()
        finally:
            link.close()
        return made

    def _fetch(self, stream) -> str:
        from . import fetch as _fetch

        folder = self.opts["folder"] or str(Path(self.profile["folder"]) / FOLDER / "streams")
        return _fetch.download(stream["url"], folder, log=self.log,
                               progress=lambda share, note="": self.progress(share * 0.4, note),
                               cancel=self.cancel)

    def _find(self, path) -> list:
        count = int(self.opts["per_stream"])
        low, high = float(self.opts["min_len"]), float(self.opts["max_len"])
        if self.opts["sense"]:
            found = self._find_by_sense(path, count, low, high)
            if found:
                return found
        from . import loudness as _loud

        data = _loud.audio_levels(path, progress=lambda done, total: self.progress(
            0.4 + 0.1 * (done / total if total else 0.0), "звук"))
        return [{"start": item["start"], "end": item["end"], "score": item["score"],
                 "title": ""} for item in _loud.pick_moments(data, count, low, high)]

    def _find_by_sense(self, path, count, low, high) -> list:
        import contextlib
        import io

        from clipper.backends import get_backend
        from clipper.models import Task
        from clipper.pipeline import process
        from clipper.store import Store

        cfg = studio_config(self.profile)
        cfg.min_clip_s, cfg.max_clip_s, cfg.top_n = low, high, count
        backend = get_backend(cfg)
        cfg.ensure_dirs()
        with contextlib.redirect_stdout(io.StringIO()):
            result = process(Task(source=Path(path)), cfg, Store(cfg.db_path),
                             backend=backend, do_render=False, verbose=False,
                             cancel=self.cancel)
        best = sorted(result.candidates, key=lambda c: c.score, reverse=True)[:count]
        return [{"start": c.start, "end": c.end, "score": c.score, "title": c.title}
                for c in best]

    def _check(self, path, moments, donations=None, away=None) -> list:
        from . import streamcheck as _check

        context = _check.Context(path, self.profile, log=self.log, cancel=self.cancel)
        kept, report, _events, _gone = _check.check_moments(
            context, moments, judge=self.judge,
            donations=self.opts["donations"] if donations is None else donations,
            away=self.opts["away"] if away is None else away)
        for line in report:
            self.log("  " + line)
        return kept

    def _render(self, stream, path, moments) -> list:
        folder = Path(self.profile["folder"]) / FOLDER / ("%s_%s" % (
            time.strftime("%Y%m%d"), stream["vod"]))
        folder.mkdir(parents=True, exist_ok=True)
        timecodes = folder / "_моменты.txt"
        _hand.write_timecodes(str(timecodes), moments)
        command = render_command(self.profile, path, timecodes, folder)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace",
                                   env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                                   **_ff._NOWIN)
        names = []
        tail = []
        for line in process.stdout:
            line = line.rstrip()
            hit = AUTO_NAME.search(line)
            if hit:
                names.append(str(folder / hit.group(1)))
                self.log("  смонтирован: %s" % hit.group(1))
                self.progress(0.5 + 0.5 * len(names) / max(1, len(moments)), "монтаж")
            elif line:
                tail = (tail + [line])[-5:]
            if self.cancel():
                process.kill()
                raise RuntimeError("остановлено")
        process.wait()
        if process.returncode != 0 and not names:
            raise RuntimeError("монтаж не удался: %s" % " | ".join(tail))
        return names

    def post_next(self, now=None, force=False, kind=CLIP):
        now = time.time() if now is None else now
        if not force and now < next_post_at(self.profile, now, kind):
            return None
        waiting = approved(self.profile, kind)
        if not waiting:
            return None
        item = waiting[0]
        fixed = self.opts["title"].strip() if kind == CLIP else ""
        title = fixed or item["title"] or nice_title(item["file"])
        link = connect(self.profile)
        try:
            try:
                done = self.uploader(item, title)
            except Exception as error:
                link.execute("UPDATE queue SET status = ?, error = ? WHERE id = ?",
                             (FAILED, str(error)[:500], item["id"]))
                link.commit()
                raise
            link.execute("UPDATE queue SET status = ?, posted_at = ?, url = ?, error = ''"
                         " WHERE id = ?", (POSTED, now, (done or {}).get("url") or "",
                                           item["id"]))
            link.commit()
        finally:
            link.close()
        self.log("выложен: %s" % title)
        return item["id"]

    def _upload(self, item, title):
        from . import upload as _up

        if not _up.browser():
            try:
                from studio.window import load_settings
                chosen = load_settings().get("upload_browser")
            except Exception:
                chosen = ""
            _up.set_browser(chosen or _up.default_browser())
        rolik = (item.get("kind") or CLIP) == ROLIK
        done = _up.upload("youtube" if rolik else self.opts["service"], self.opts["account"],
                          item["file"], title,
                          visibility=self.opts["rolik_visibility" if rolik else "visibility"],
                          headed=True, log=self.log, cancel=self.cancel,
                          channel=self.opts["channel"])
        _up.record(_up.db_for(self.profile), done,
                   origin={"source": "", "start": item["start"], "finish": item["finish"]})
        return done

    def after_live(self, started=0.0, tries=AFTER_LIVE_TRIES, wait=AFTER_LIVE_WAIT):
        """Эфир кончился: ждать, пока твич отдаст запись, и сразу её нарезать."""
        for attempt in range(int(tries)):
            self.sleep(wait if attempt else min(wait, 60.0))
            if self.cancel():
                return None
            try:
                found = self.listed()
            except Exception as error:
                self.log("список записей не получен: %s" % str(error).splitlines()[0][:120])
                continue
            fresh = [item for item in found if is_this_stream(item, started)]
            if not started:
                link = connect(self.profile)
                try:
                    fresh = [item for item in found if not link.execute(
                        "SELECT 1 FROM streams WHERE vod = ?", (item["vod"],)).fetchone()]
                finally:
                    link.close()
            if fresh:
                self.log("запись эфира появилась: %s" % (fresh[0]["title"] or fresh[0]["vod"]))
                return self.run(post=False, wait=True)
            self.log("записи эфира на канале пока нет, проверю через %d мин" % (wait // 60))
        self.log("твич так и не отдал запись эфира — её подхватит обычный запуск")
        return None

    def run(self, post=True, wait=False) -> dict:
        lock = processing_lock()
        if not lock.take():
            if not wait:
                self.log("другой процесс уже режет стрим — этот запуск пропускаю")
                return {"new": 0, "queued": 0, "posted": None, "errors": [], "busy": True}
            self.log("другой стрим ещё режется — жду очереди")
            if not lock.wait(cancel=self.cancel):
                return {"new": 0, "queued": 0, "posted": None, "errors": [], "busy": True}
        try:
            return self._run(post)
        finally:
            lock.free()

    def _run(self, post) -> dict:
        report = {"new": 0, "queued": 0, "posted": None, "errors": []}
        if self.opts["twitch"]:
            try:
                report["new"] = len(self.scan())
            except Exception as error:
                report["errors"].append("список записей: %s" % error)
                self.log("не получил список записей: %s" % error)
        for stream in self.waiting_streams()[:int(self.opts["streams_per_run"])]:
            if self.cancel():
                break
            try:
                report["queued"] += len(self.process(stream))
            except Exception as error:
                report["errors"].append("%s: %s" % (stream.get("title") or stream["vod"], error))
                self.log("запись не обработана: %s" % error)
        if post and not self.cancel():
            try:
                report["posted"] = self.post_next()
            except Exception as error:
                report["errors"].append("выгрузка: %s" % error)
                self.log("выгрузка не удалась: %s" % error)
        return report


TASK_NAME = "AstraStudioAutopilot"
LAUNCHER = "autopilot.bat"
LAUNCHER_TEXT = "\r\n".join((
    "@echo off",
    "cd /d \"%~dp0\"",
    "set VENV=.venv\\Scripts\\pythonw.exe",
    "if not exist \"%VENV%\" set VENV=..\\astra\\.venv\\Scripts\\pythonw.exe",
    "if exist \"%VENV%\" (",
    "    \"%VENV%\" -m core.autopilot run",
    ") else (",
    "    pythonw -m core.autopilot run",
    ")",
    "",
))


def launcher(root=None) -> str:
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    path = root / LAUNCHER
    if not path.is_file() or path.read_text(encoding="ascii", errors="replace") != LAUNCHER_TEXT:
        path.write_text(LAUNCHER_TEXT, encoding="ascii", newline="")
    return str(path)


def task_on_command(path, every_hours=1) -> list:
    return ["schtasks", "/Create", "/F", "/SC", "HOURLY", "/MO", str(int(every_hours)),
            "/TN", TASK_NAME, "/TR", '"%s"' % path]


def task_off_command() -> list:
    return ["schtasks", "/Delete", "/F", "/TN", TASK_NAME]


def task_query_command() -> list:
    return ["schtasks", "/Query", "/TN", TASK_NAME]


def task_run(command) -> tuple:
    done = subprocess.run(command, capture_output=True, **_ff._NOWIN)
    text = (done.stdout or b"") + (done.stderr or b"")
    for codec in ("cp866", "cp1251", "utf-8"):
        try:
            return done.returncode, text.decode(codec).strip()
        except UnicodeDecodeError:
            continue
    return done.returncode, text.decode("utf-8", "replace").strip()


def nice_title(path) -> str:
    name = os.path.splitext(os.path.basename(str(path)))[0]
    name = re.sub(r"^\d+_\d{2}-\d{2}-\d{2}_", "", name)
    return name.replace("_", " ").strip() or "Клип"


def studio_config(profile=None):
    from clipper.config import Config, with_profile

    try:
        from studio.window import load_settings
        saved = load_settings()
    except Exception:
        saved = {}
    cfg = Config()
    cfg.backend = saved.get("backend") or cfg.backend
    if cfg.backend == "openai-compat":
        cfg.local_model = saved.get("openai_model") or cfg.local_model
        cfg.openai_base_url = saved.get("openai_url") or cfg.openai_base_url
        cfg.openai_api_key = saved.get("openai_key") or cfg.openai_api_key
    else:
        cfg.local_model = saved.get("local_model") or cfg.local_model
    if profile and profile.get("whisper"):
        cfg.whisper_model = profile["whisper"]
    return with_profile(cfg)


def judge_for(profile):
    from . import donations as _don

    try:
        from clipper.backends import get_backend

        cfg = studio_config(profile)
        return _don.judge_with(get_backend(cfg), cfg)
    except Exception:
        return _don.judge_by_words


def main(argv=None) -> int:
    from . import learnlog as _log
    from . import profiles as _pro

    parser = argparse.ArgumentParser(prog="autopilot")
    parser.add_argument("action", choices=("run", "post", "scan"))
    parser.add_argument("--profile", default="")
    args = parser.parse_args(argv)
    profile = _pro.load(args.profile) if args.profile else _pro.active()
    if not profile:
        print("нет профиля")
        return 1

    def say(text):
        print(text, flush=True)
        _log.write(profile["folder"], "автопилот: %s" % text)

    pilot = Pilot(profile, log=say, judge=judge_for(profile))
    if args.action == "scan":
        pilot.scan()
        return 0
    if args.action == "post":
        pilot.post_next()
        return 0
    report = pilot.run()
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
