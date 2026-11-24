"""Запись звука эфира: твич глушит музыку только в записи, а в эфире звук целый.

Сторож раз в минуту спрашивает твич, идёт ли эфир на канале. Пока идёт — streamlink
пишет только звук (около 100 МБ в час, рекламные вставки твича он выкидывает сам).
Твич отдаёт звук кусочками MP4 с метками времени от начала эфира; на месте вырезанной
рекламы в метках остаётся дыра — при чтении она заполняется тишиной, чтобы секунда
записи оставалась секундой эфира.
Оборвалась связь — запись продолжается новым куском. Рядом с каждым куском лежит
.json: канал, номер эфира и когда кусок начал писаться по часам компьютера.

После эфира Студия качает запись твича как обычно. На заглушённых местах звук берётся
из своей записи эфира: грубо место находится по часам (запись твича начинается с
начала эфира), точно — по звуку рядом с мутом, который в обеих записях целый.
"""

from __future__ import annotations

import calendar
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import ffmpeg as _ff
from . import mute as _mute

FOLDER = "_эфир"
HOME_FOLDER = "Astra"     # папка для записей, если своя папка для стримов не выбрана
DEFAULT_CHANNEL = ""
QUALITY = "audio_only,worst"
POLL = 60.0               # раз в столько секунд спрашивать, идёт ли эфир
TICK = 2.0                # так часто сторож смотрит на идущую запись
WORKERS = 6               # столько кусков записи твича качается разом
LATE = 120.0              # Студия открыта позже начала эфира — начало докачивается
VODS_QUERY = ('{ user(login: "%s") { videos(first: 3, type: ARCHIVE) { edges { node '
              '{ id status createdAt } } } } }')
LIVE_QUERY = '{ user(login: "%s") { stream { id createdAt } } }'
VOD_QUERY = '{ video(id: "%s") { createdAt lengthSeconds owner { login } } }'

RATE = 8000
PROBE = 20.0              # столько звука рядом с мутом сверяется с записью эфира
PROBE_GAP = 2.0           # … отступив от края мута
LATENCY = 8.0             # эфир доходит до компьютера с задержкой в несколько секунд
REACH = 150.0             # насколько грубое место по часам может ошибаться
AGREE = 0.5               # сверки с двух сторон мута сходятся до полсекунды
LEAST_SCORE = 0.35
QUIET_LEVEL = 1e-3        # тише этого (по громкости) звук считается тишиной        # ниже этого звук рядом с мутом на запись эфира не похож
PAD = 1.0                 # вырезанный звук берётся с запасом с краёв
EXT = ".m4a"
EXTS = (".m4a", ".ts")
FILL_GAPS = "aresample=async=1:first_pts=0,apad"


def _ask(body):
    request = urllib.request.Request(_mute.GQL, data=json.dumps(body).encode("utf-8"),
                                     headers={"Client-ID": _mute.CLIENT_ID,
                                              "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as answer:
        return json.loads(answer.read().decode("utf-8"))


def channel_of(text) -> str:
    """Канал из ссылки на эфир или просто имени: twitch.tv/somestreamer → somestreamer."""
    text = str(text or "").strip()
    found = re.search(r"twitch\.tv/([A-Za-z0-9_]{2,25})", text)
    if found and found.group(1).lower() not in ("videos", "directory", "settings"):
        return found.group(1).lower()
    return text.lower() if re.fullmatch(r"[A-Za-z0-9_]{2,25}", text) else ""


def epoch(stamp) -> float:
    """«2026-11-05T15:02:11Z» → секунды UTC."""
    return float(calendar.timegm(time.strptime(str(stamp)[:19], "%Y-%m-%dT%H:%M:%S")))


def live_now(channel, ask=None):
    """Идёт ли эфир: {"id", "started"} или None."""
    data = (ask or _ask)({"query": LIVE_QUERY % channel})
    user = (data.get("data") or {}).get("user") or {}
    stream = user.get("stream")
    if not stream:
        return None
    return {"id": str(stream.get("id") or ""), "started": epoch(stream["createdAt"])}


def vod_info(vod, ask=None):
    """Когда началась запись твича и чья она: {"started", "length", "channel"} или None."""
    data = (ask or _ask)({"query": VOD_QUERY % vod})
    video = (data.get("data") or {}).get("video")
    if not video or not video.get("createdAt"):
        return None
    return {"started": epoch(video["createdAt"]),
            "length": float(video.get("lengthSeconds") or 0),
            "channel": ((video.get("owner") or {}).get("login") or "").lower()}


def folder_of(dldir) -> str:
    return os.path.join(str(dldir), FOLDER)


def default_dldir() -> str:
    """Куда писать эфир, когда папка для стримов ещё не выбрана: «Видео/Astra».

    Чтобы хватило одной ссылки: вставил — и запись пошла, папку выберете потом.
    """
    home = os.path.expanduser("~")
    for name in ("Videos", "Видео"):
        place = os.path.join(home, name)
        if os.path.isdir(place):
            return os.path.join(place, HOME_FOLDER)
    return os.path.join(home, HOME_FOLDER)


def streamlink() -> str:
    """streamlink лежит рядом с питоном Студии, если не найден в PATH."""
    found = shutil.which("streamlink")
    if found:
        return found
    here = os.path.dirname(sys.executable)
    for place in (here, os.path.join(here, "Scripts")):
        for name in ("streamlink.exe", "streamlink"):
            path = os.path.join(place, name)
            if os.path.isfile(path):
                return path
    return ""


# ---------------------------------------------------------------- начало эфира


def recording_vod(channel, stream, ask=None):
    """Запись твича, которая пишется прямо сейчас для этого эфира: {"id", "started"}."""
    data = (ask or _ask)({"query": VODS_QUERY % channel})
    user = (data.get("data") or {}).get("user") or {}
    for edge in ((user.get("videos") or {}).get("edges") or ()):
        node = edge.get("node") or {}
        if node.get("status") != "RECORDING" or not node.get("createdAt"):
            continue
        started = epoch(node["createdAt"])
        if stream is None or abs(started - stream["started"]) < 900:
            return {"id": str(node["id"]), "started": started}
    return None


def audio_playlist(vod, runner=None, tries=10):
    """Ссылка на звук записи твича через yt-dlp; твич иногда рвёт связь — с повторами."""
    from . import fetch as _fetch

    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True,
                                                encoding="utf-8", errors="replace",
                                                env=_fetch.tool_env(), **_ff._NOWIN))
    last = [""]
    for attempt in range(tries):
        done = run([_ff.find(_fetch.TOOL), "-f", "Audio_Only", "-g",
                    "https://www.twitch.tv/videos/%s" % vod])
        if done.returncode == 0 and done.stdout.strip():
            return done.stdout.strip().splitlines()[0]
        last = (done.stderr or "").strip().splitlines()[-1:] or [""]
        time.sleep(min(10, 2 + attempt))
    raise RuntimeError("твич не дал звук записи: %s" % last[0][:150])


class Gone(RuntimeError):
    """Куска больше нет: после эфира твич переименовывает заглушённые куски в «-muted»."""


def _download(url, tries=8, timeout=30):
    import urllib.error

    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as answer:
                return answer.read()
        except urllib.error.HTTPError as error:
            if error.code in (403, 404, 410):
                raise Gone(url.rsplit("/", 1)[-1])
            last = error
        except Exception as error:
            last = error
        time.sleep(min(5, 1 + attempt))
    raise RuntimeError("не скачалось %s: %s" % (url.rsplit("/", 1)[-1], last))


def fetch_start(channel, folder, stream, ask=None, playlist=audio_playlist, get=_download,
                log=None, progress=None, cancel=None):
    """Звук эфира с самого начала до сейчас — из записи твича, которая ещё пишется.

    Пока эфир идёт, твич звук в записи ещё не глушит. Каждый кусок качается с
    повторами: yt-dlp при обрывах молча пропускает куски. Ответ: путь или "".
    """
    log = log or (lambda text: None)
    progress = progress or (lambda done, total: None)
    vod = recording_vod(channel, stream, ask=ask)
    if not vod:
        log("у эфира нет записи на твиче — начало не докачать")
        return ""
    url = playlist(vod["id"])
    lines = get(url).decode("utf-8", "replace").splitlines()
    base = url.rsplit("/", 1)[0] + "/"
    init = next((line.split('"')[1] for line in lines if line.startswith("#EXT-X-MAP")), "")
    names = [line for line in lines if line and not line.startswith("#")]
    if not names:
        log("в записи твича пока пусто")
        return ""
    muted = sum("muted" in name for name in names)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "%s_%s_начало%s" % (
        channel, (stream or {}).get("id") or vod["id"], EXT))
    temp = path + ".part"
    stopped = False
    gone = []

    def piece(name):
        try:
            return get(base + name)
        except Gone:                              # заглушён и переименован — на его месте тишина
            gone.append(name)
            return b""

    try:
        with open(temp, "wb") as out, ThreadPoolExecutor(WORKERS) as pool:
            if init:
                out.write(get(base + init))
            for start in range(0, len(names), WORKERS * 4):   # кусками, по порядку
                if cancel and cancel():
                    stopped = True
                    break
                batch = names[start:start + WORKERS * 4]
                for data in pool.map(piece, batch):
                    out.write(data)
                progress(start + len(batch), len(names))
    except BaseException:
        if os.path.isfile(temp):                  # упало посреди — недокачанное не копится
            os.unlink(temp)
        raise
    if stopped:                                   # остановили посреди — недокачанное убрать
        os.unlink(temp)
        return ""
    os.replace(temp, path)
    write_meta(path, {"channel": channel, "stream": (stream or {}).get("id", ""),
                      "vod": vod["id"], "stream_started": (stream or {}).get("started"),
                      "started": vod["started"] + LATENCY, "start": True})
    muted += len(gone)
    log("начало эфира докачано: %d кусков%s" % (
        len(names), ", %d уже заглушены твичем" % muted if muted else ""))
    return path


# ---------------------------------------------------------------- запись


def meta_path(path) -> str:
    return str(path) + ".json"


def read_meta(path):
    try:
        with open(meta_path(path), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def write_meta(path, meta):
    with open(meta_path(path), "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False)


def recordings(folder) -> list:
    """Куски записи эфира в папке: [(путь, meta)] по времени начала."""
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        if not name.endswith(EXTS):
            continue
        path = os.path.join(folder, name)
        meta = read_meta(path)
        if meta and meta.get("started") and os.path.getsize(path) > 0:
            out.append((path, meta))
    return sorted(out, key=lambda item: item[1]["started"])


def finished_text(done) -> str:
    """Строка для уведомления о конце эфира: сколько записалось и куда легло."""
    if not done:
        return ""
    size = done.get("bytes", 0) / float(1 << 20)
    parts = done.get("parts", 0)
    return ("Эфир %s закончился. Записано %s звука (%.0f МБ%s), файлы в папке %s."
            % (done.get("channel", ""), _ff.format_hms(done.get("seconds", 0)), size,
               ", %d куска" % parts if parts > 1 else "", done.get("folder", "")))


class Recorder:
    """Один кусок записи: процесс streamlink и его .json."""

    def __init__(self, channel, folder, stream=None, binary=None, popen=None, clock=None):
        self.channel = channel
        self.clock = clock or time.time
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(self.clock()))
        self.path = os.path.join(folder, "%s_%s%s" % (channel, stamp, EXT))
        self.meta = {"channel": channel, "stream": (stream or {}).get("id", ""),
                     "stream_started": (stream or {}).get("started"),
                     "asked": self.clock(), "started": None}
        write_meta(self.path, self.meta)
        cmd = [binary or streamlink(), "--retry-open", "3", "--hls-live-edge", "2",
               "https://www.twitch.tv/%s" % channel, QUALITY, "-o", self.path, "-f"]
        self.proc = (popen or subprocess.Popen)(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_ff._NOWIN)

    def size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def tick(self):
        """Первые байты в файле — момент начала куска по часам компьютера."""
        if self.meta["started"] is None and self.size() > 0:
            self.meta["started"] = self.clock()
            write_meta(self.path, self.meta)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self):
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
        self.tick()
        if self.meta["started"] is None:          # так ничего и не записалось
            for path in (self.path, meta_path(self.path)):
                if os.path.isfile(path):
                    os.unlink(path)


class Watcher:
    """Сторож эфира: пока включён, сам начинает и продолжает запись.

    on_state(text) получает строку для окна; зовётся из потока сторожа.
    """

    def __init__(self, channel, folder, on_state=None, ask=None, recorder=Recorder,
                 poll=POLL, tick=TICK, clock=None, sleep=None, catcher=fetch_start,
                 threads=True):
        self.channel = channel
        self.folder = folder
        self.on_state = on_state or (lambda text: None)
        self.ask = ask
        self.recorder = recorder
        self.poll, self.tick_every = poll, tick
        self.clock = clock or time.time
        self.sleep = sleep
        self.current = None
        self.parts = 0
        self.stream = None
        self.catcher = catcher
        self.threads = threads
        self.caught = set()
        self.catch_text = ""
        self.wrote = {"parts": 0, "bytes": 0, "from": None, "till": None, "stream": None}
        self.finished = None      # последний закончившийся эфир: что записалось
        self.finishes = 0         # сколько эфиров закончилось — окно ловит новое число
        self._stop = threading.Event()
        self._poke = threading.Event()
        self._thread = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._poke.set()
        if self.current:
            self.current.stop()
            self.current = None
        self.on_state("запись эфира выключена")

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    def _wait(self, seconds) -> bool:
        if self.sleep:
            self.sleep(seconds)
            return self._stop.is_set()
        self._poke.wait(seconds)
        self._poke.clear()
        return self._stop.is_set()

    def poke(self):
        """Анонс в телеграме: спросить твич сейчас, не дожидаясь минуты."""
        self._poke.set()

    def step(self):
        """Один шаг сторожа. Ответ: через сколько секунд шагнуть снова."""
        if self.current and self.current.alive():
            self.current.tick()
            self.on_state("идёт запись эфира %s: %s, %d МБ%s" % (
                self.channel, _ff.format_hms(self.clock() - (self.current.meta["started"]
                                                             or self.clock())),
                self.current.size() // (1 << 20),
                " · " + self.catch_text if self.catch_text else ""))
            return self.tick_every
        if self.current:                          # streamlink вышел: конец эфира или обрыв
            self._wrote_down(self.current)
            self.current.stop()
            self.current = None
        try:
            self.stream = live_now(self.channel, ask=self.ask)
        except Exception as error:
            self.on_state("твич не ответил (%s), спрошу через минуту" %
                          str(error).splitlines()[0][:80])
            return self.poll
        if not self.stream:
            self._finish()                        # эфира больше нет — значит, он кончился
            self.on_state("%s не в эфире — проверяю раз в минуту" % self.channel)
            return self.poll
        self.parts += 1
        self.current = self.recorder(self.channel, self.folder, stream=self.stream)
        self.on_state("эфир %s начался — пишу звук" % self.channel)
        if (self.catcher and self.stream.get("id") not in self.caught
                and self.clock() - self.stream["started"] > LATE):
            self.caught.add(self.stream.get("id"))
            if self.threads:
                threading.Thread(target=self._catch, args=(dict(self.stream),),
                                 daemon=True).start()
            else:
                self._catch(dict(self.stream))
        return self.tick_every

    def _wrote_down(self, part):
        """Кусок дописан: его размер и время идут в счёт эфира."""
        size = part.size() if callable(getattr(part, "size", None)) else 0
        if size <= 0 or not getattr(part, "meta", {}).get("started"):
            return
        self.wrote["parts"] += 1
        self.wrote["bytes"] += size
        began = part.meta["started"]
        if self.wrote["from"] is None or began < self.wrote["from"]:
            self.wrote["from"] = began
        self.wrote["till"] = self.clock()
        if part.meta.get("stream_started") and not self.wrote["stream"]:
            self.wrote["stream"] = part.meta["stream_started"]

    def _finish(self):
        """Эфир кончился: что записалось — наружу, счёт обнуляется."""
        if not self.wrote["parts"]:
            return
        self.finished = {"channel": self.channel, "folder": self.folder,
                         "parts": self.wrote["parts"], "bytes": self.wrote["bytes"],
                         "seconds": max(0.0, (self.wrote["till"] or 0)
                                        - (self.wrote["from"] or 0)),
                         "started": self.wrote["stream"] or 0.0, "at": self.clock()}
        self.finishes += 1
        self.wrote = {"parts": 0, "bytes": 0, "from": None, "till": None, "stream": None}
        self.on_state("эфир %s закончился, запись сохранена" % self.channel)

    def _catch(self, stream):
        """Эфир шёл до включения сторожа: начало берётся из записи твича."""
        self.catch_text = "докачиваю начало эфира…"

        def progress(done, total):
            self.catch_text = "докачиваю начало эфира: %d из %d" % (done, total)

        def log(text):
            self.catch_text = text

        try:
            path = self.catcher(self.channel, self.folder, stream, ask=self.ask, log=log,
                                progress=progress, cancel=self._stop.is_set)
            if not path and not self._stop.is_set():
                self.caught.discard(stream.get("id"))
        except Exception as error:
            self.catch_text = "начало эфира не докачалось: %s" % str(error).splitlines()[0][:80]
            self.caught.discard(stream.get("id"))      # попробует при следующем куске

    def _run(self):
        while not self._stop.is_set():
            try:
                pause = self.step()
            except Exception as error:
                self.on_state("сторож эфира споткнулся: %s" % str(error).splitlines()[0])
                pause = self.poll
            if self._wait(pause):
                break


# ---------------------------------------------------------------- звук в мут


def read_audio(path, start, length, rate=RATE) -> np.ndarray:
    """Звук по меткам времени файла: дыры в записи эфира приходят тишиной."""
    cmd = [_ff.binary("ffmpeg"), "-hide_banner", "-loglevel", "error",
           "-ss", "%.3f" % max(0.0, start), "-t", "%.3f" % length, "-i", str(path),
           "-vn", "-af", FILL_GAPS, "-ac", "1", "-ar", str(rate), "-t", "%.3f" % length,
           "-f", "s16le", "-"]
    done = subprocess.run(cmd, capture_output=True, **_ff._NOWIN)
    out = np.frombuffer(done.stdout, np.int16).astype(np.float32) / 32768.0
    return out[:int(round(length * rate))]


def length_of(path) -> float:
    """Сколько секунд в записи эфира. У кусочков MP4 твича «длительность» в заголовке
    считается от начала эфира, поэтому длина берётся по самим меткам."""
    cmd = [_ff.binary("ffmpeg"), "-hide_banner", "-nostats", "-stats_period", "3600",
           "-i", str(path), "-vn", "-c", "copy", "-f", "null", "-"]
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **_ff._NOWIN)
    found = re.findall(r"time=(\d+):(\d+):([\d.]+)", done.stderr)
    if not found:
        return _ff.duration(path)
    h, m, sec = found[-1]
    return int(h) * 3600 + int(m) * 60 + float(sec)


def match(probe, window):
    """Где probe лучше всего ложится в window: (сдвиг в отсчётах, сходство 0..1)."""
    probe = probe - probe.mean()
    if len(window) < len(probe) or not np.any(probe):
        return 0, 0.0
    size = 1
    while size < len(window) + len(probe):
        size *= 2
    spectrum = np.fft.rfft(window, size) * np.conj(np.fft.rfft(probe, size))
    corr = np.fft.irfft(spectrum, size)[:len(window) - len(probe) + 1]
    sums = np.concatenate([[0.0], np.cumsum(window)])
    squares = np.concatenate([[0.0], np.cumsum(window ** 2)])
    n = len(probe)
    mean = (sums[n:] - sums[:-n]) / n
    energy = squares[n:] - squares[:-n] - n * mean ** 2
    score = corr / (np.sqrt(np.maximum(energy, 1e-12)) * np.sqrt(np.sum(probe ** 2)))
    score[energy < n * QUIET_LEVEL ** 2] = 0.0      # тишина в записи ни на что не похожа
    best = int(np.argmax(score))
    return best, float(score[best])


def offset_near(stream, record, at, guess, read=read_audio, reach=REACH, rate=RATE):
    """Сдвиг «время в записи твича − время в записи эфира» по звуку возле at.

    guess — сдвиг по часам. Ответ: (сдвиг, сходство) или (None, 0).
    """
    if at < 0:
        return None, 0.0
    probe = read(stream, at, PROBE, rate)
    if len(probe) < PROBE * rate * 0.9 or _mute.rms(probe) <= _mute.QUIET:
        return None, 0.0
    first = at - guess - reach
    window = read(record, first, PROBE + 2 * reach, rate)
    if first < 0:                                  # запись эфира начата позже
        window = np.concatenate([np.zeros(int(-first * rate), np.float32), window])
    shift, score = match(probe, window)
    if score < LEAST_SCORE:
        return None, score
    return at - (first + shift / float(rate)), score


def place_span(stream, record, start, end, guess, read=read_audio, log=None):
    """Куски мута [start, end) и откуда их брать в записи эфира: [(от, до, сдвиг)]."""
    log = log or (lambda text: None)
    before, score_b = offset_near(stream, record, start - PROBE_GAP - PROBE, guess, read)
    after, score_a = offset_near(stream, record, end + PROBE_GAP, guess, read)
    if before is None and after is None:
        log("мут %s–%s: в записи эфира это место не нашлось" % (
            _ff.format_hms(start), _ff.format_hms(end)))
        return []
    if before is None or after is None:
        return [(start, end, before if after is None else after)]
    if abs(before - after) <= AGREE:
        return [(start, end, (before + after) / 2.0)]
    middle = (start + end) / 2.0                  # внутри мута запись эфира рвалась
    log("мут %s–%s: в записи эфира внутри пропуск %.0f с — звук с двух сторон" % (
        _ff.format_hms(start), _ff.format_hms(end), abs(after - before)))
    return [(start, middle, before), (middle, end, after)]


def covering(spans, start, end) -> list:
    return [(max(a, start), min(b, end)) for a, b in spans if min(b, end) - max(a, start) > 0.5]


def cut_piece(record, start, length, target):
    cmd = [_ff.binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-ss", "%.3f" % max(0.0, start), "-t", "%.3f" % length, "-i", str(record),
           "-vn", "-af", FILL_GAPS, "-ac", "2", "-ar", "48000", "-t", "%.3f" % length,
           "-c:a", "pcm_s16le", str(target)]
    subprocess.run(cmd, capture_output=True, **_ff._NOWIN)
    return os.path.isfile(target) and os.path.getsize(target) > 1000


def live_cache(stream) -> str:
    return str(stream) + ".live.json"


def patches_for(stream, folder, ask=None, read=read_audio, cut=cut_piece, log=None) -> list:
    """Заплатки мута из записи эфира в формате core.mute.patches (время — по стриму).

    Пусто, если мута нет, записи эфира нет или она не про этот стрим.
    """
    log = log or (lambda text: None)
    records = recordings(folder)
    if not records:
        return []
    stamp = [[os.path.basename(path), os.path.getsize(path)] for path, _ in records]
    try:
        with open(live_cache(stream), encoding="utf-8") as handle:
            saved = json.load(handle)
        if saved.get("records") == stamp and all(os.path.isfile(p["clip"])
                                                 for p in saved["patches"]):
            return saved["patches"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    muted = _mute.muted_ranges(stream, ask=ask, log=log)
    if not muted:
        return []
    vod = _mute.vod_id(_mute.original_of(stream))
    if not vod:
        log("у стрима нет номера записи твича — запись эфира не к чему привязать")
        return []
    try:
        info = vod_info(vod, ask=ask)
    except Exception as error:
        log("твич не сказал, когда начался стрим: %s" % str(error).splitlines()[0])
        return []
    if not info:
        return []
    length = info["length"] or _ff.duration(stream)
    pieces = os.path.join(folder, "_куски")
    os.makedirs(pieces, exist_ok=True)
    out = []
    for path, meta in records:
        if info["channel"] and meta.get("channel") and meta["channel"] != info["channel"]:
            continue
        rec_len = length_of(path)
        # секунда стрима = секунда записи эфира + guess
        guess = meta["started"] - info["started"] - LATENCY
        spans = covering(muted, guess, guess + rec_len)
        if out:                                    # куски записи перекрываются — второй
            spans = [(p["start"], p["end"]) for p in   # раз одно место не закрывается
                     without([{"start": x, "end": y, "from": 0.0, "rate": 1.0}
                              for x, y in spans], out)]
        if not spans or guess > length:
            continue
        log("запись эфира %s покрывает %d мест мута" % (os.path.basename(path), len(spans)))
        gains, mine = [], []
        for start, end in spans:
            for a, b, shift in place_span(stream, path, start, end, guess, read, log):
                take = a - shift - PAD
                target = os.path.join(pieces, "%s_%d.wav" % (vod, int(round(a * 10))))
                if not cut(path, take, b - a + 2 * PAD, target):
                    log("кусок записи эфира %s не вырезался" % _ff.format_hms(a))
                    continue
                gains.append(_level(stream, path, a, shift, read))
                mine.append({"start": a, "end": b, "from": PAD + min(0.0, take),
                             "rate": 1.0, "clip": target, "gain": 1.0, "live": True})
        level = float(np.median([g for g in gains if g])) if any(gains) else 1.0
        for patch in mine:
            patch["gain"] = float(np.clip(level, *_mute.GAIN_RANGE))
        out.extend(mine)
    out.sort(key=lambda p: p["start"])
    if out:
        log("звук мута из записи эфира: %d мест, %.0f с" % (
            len(out), sum(p["end"] - p["start"] for p in out)))
        try:
            with open(live_cache(stream), "w", encoding="utf-8") as handle:
                json.dump({"records": stamp, "patches": out}, handle, ensure_ascii=False)
        except OSError:
            pass
    return out


def _level(stream, record, at, shift, read):
    """Во сколько раз стрим громче записи эфира рядом с мутом."""
    spot = at - PROBE_GAP - PROBE
    ours = _mute.rms(read(stream, spot, PROBE, RATE))
    theirs = _mute.rms(read(record, spot - shift, PROBE, RATE))
    return ours / theirs if ours > _mute.QUIET and theirs > _mute.QUIET else 0.0


def without(found, taken) -> list:
    """Заплатки found (нарезчика) без мест, которые уже закрыты заплатками taken."""
    out = []
    for patch in found:
        pieces = [(patch["start"], patch["end"])]
        for other in taken:
            nxt = []
            for a, b in pieces:
                x, y = other["start"], other["end"]
                if y <= a or x >= b:
                    nxt.append((a, b))
                    continue
                if x > a:
                    nxt.append((a, x))
                if y < b:
                    nxt.append((y, b))
            pieces = nxt
        for a, b in pieces:
            if b - a < 0.5:
                continue
            item = dict(patch, start=a, end=b)
            item["from"] = patch["from"] + (a - patch["start"]) / patch["rate"]
            out.append(item)
    return out
