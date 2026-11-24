from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

from . import ffmpeg as _ff

TOOL = "yt-dlp"
NAME_TEMPLATE = "%(title).150B [%(id)s].%(ext)s"
QUALITIES = (
    ("Лучшее", "bv*+ba/b"),
    ("до 1080p", "bv*[height<=1080]+ba/b[height<=1080]/b"),
    ("до 720p", "bv*[height<=720]+ba/b[height<=720]/b"),
    ("только звук", "ba[ext=m4a]/ba/b"),
)
QUALITY_TITLES = [title for title, _ in QUALITIES]
_SHARE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
_SPEED = re.compile(r"\bat\s+([\d.]+)\s*([KMG]i?B)/s", re.IGNORECASE)
_LEFT = re.compile(r"\bETA\s+((?:\d+:)?\d+:\d+)")
_WHOLE = re.compile(r"\bof\s+~?\s*([\d.]+)\s*([KMG]i?B)", re.IGNORECASE)
_UNITS = {"KIB": 1024.0, "MIB": 1024.0 ** 2, "GIB": 1024.0 ** 3,
          "KB": 1000.0, "MB": 1000.0 ** 2, "GB": 1000.0 ** 3}
_LINK = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)
_MOMENT = re.compile(r"^out_time_us=(\d+)", re.MULTILINE)
_FFMPEG_ARGS = "ffmpeg:-progress pipe:1 -nostats"
_FFMPEG_INPUT = "ffmpeg_i:-seg_max_retry 10"
_RETRIES = ("--retries", "20", "--fragment-retries", "20", "--extractor-retries", "5")
BROKEN_NATIVE = "initialization fragment found after media fragments"
FLAKY = ("unexpected_eof", "eof occurred", "connection reset", "connection aborted",
         "remotedisconnected", "incompleteread", "timed out", "10054", "10060",
         "temporary failure", "getaddrinfo failed")
ATTEMPTS = 12
PAUSE = 5.0
_FF_EXIT = re.compile(r"ffmpeg exited with code (-?\d+)")
AVERROR_EOF = -0x20464F45


class Missing(RuntimeError):
    pass


def short_path(path) -> str:
    if sys.platform != "win32":
        return str(path)
    import ctypes

    size = ctypes.windll.kernel32.GetShortPathNameW(str(path), None, 0)
    if not size:
        return str(path)
    buffer = ctypes.create_unicode_buffer(size)
    ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, size)
    return buffer.value or str(path)


def ca_bundle(tool=None) -> str:
    """Сертификаты для curl внутри yt-dlp по пути без кириллицы.

    В режиме UTF-8 yt-dlp отдаёт curl путь к cacert.pem в UTF-8, а curl на Windows
    читает его в кодировке системы и не находит файл, если в пути есть кириллица
    (например, в имени пользователя). Короткое имя того же пути — латиница.
    """
    tool = tool or shutil.which(TOOL)
    if not tool:
        return ""
    scripts = os.path.dirname(os.path.realpath(str(tool)))
    for site in (os.path.join(os.path.dirname(scripts), "Lib", "site-packages"),
                 os.path.join(os.path.dirname(scripts), "site-packages")):
        place = os.path.join(site, "certifi", "cacert.pem")
        if not os.path.isfile(place):
            continue
        if place.isascii():
            return ""
        short = short_path(place)
        return short if short.isascii() else ""
    return ""


def tool_env(**extra) -> dict:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **extra)
    if "CURL_CA_BUNDLE" not in env:
        bundle = ca_bundle()
        if bundle:
            env["CURL_CA_BUNDLE"] = bundle
    return env


def available() -> bool:
    return shutil.which(TOOL) is not None


def looks_like_link(text) -> bool:
    return bool(_LINK.match(str(text or "").strip()))


def selector(quality) -> str:
    for title, rule in QUALITIES:
        if title == quality:
            return rule
    return QUALITIES[0][1]


def command(url, folder, quality=None, template=NAME_TEMPLATE,
            engine="native") -> list:
    args = [_ff.find(TOOL), "--newline", "--progress", "--no-playlist",
            "--continue", "--no-simulate", "--print", "after_move:filepath",
            "-f", selector(quality), "--merge-output-format", "mp4",
            "-o", os.path.join(str(folder), template)] + list(_RETRIES)
    if quality == "только звук":
        place = args.index("--merge-output-format")
        del args[place:place + 2]
    if engine == "ffmpeg":
        args += ["--downloader", "ffmpeg", "--downloader-args", _FFMPEG_ARGS,
                 "--downloader-args", _FFMPEG_INPUT]
    return args + ["--", str(url).strip()]


def moment(line) -> float:
    found = _MOMENT.search(str(line))
    return float(found.group(1)) / 1000000.0 if found else -1.0


def length(url) -> float:
    try:
        out = subprocess.run(
            [_ff.find(TOOL), "--no-playlist", "--skip-download",
             "--print", "%(duration)s", "--", str(url).strip()],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, **_ff._NOWIN)
    except (OSError, subprocess.SubprocessError):
        return 0.0
    for line in (out.stdout or "").splitlines():
        try:
            return float(line.strip())
        except ValueError:
            continue
    return 0.0


def share(line):
    found = _SHARE.search(str(line))
    if not found:
        return None
    try:
        return min(1.0, float(found.group(1)) / 100.0)
    except ValueError:
        return None


def _bytes(amount, unit) -> float:
    try:
        return float(amount) * _UNITS.get(str(unit).upper(), 1.0)
    except ValueError:
        return 0.0


def big(size) -> str:
    size = float(size)
    for mark, step in (("ГБ", 1000.0 ** 3), ("МБ", 1000.0 ** 2), ("КБ", 1000.0)):
        if size >= step:
            return "%.1f %s" % (size / step, mark)
    return "%d Б" % int(size)


def spell_left(clock) -> str:
    parts = [int(part) for part in str(clock).split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts
    if hours:
        return "осталось %d ч %02d мин" % (hours, minutes)
    if minutes:
        return "осталось %d мин" % minutes
    return "осталось меньше минуты" if seconds else ""


def note(line) -> str:
    said = []
    speed = _SPEED.search(str(line))
    if speed:
        said.append("%s/с" % big(_bytes(speed.group(1), speed.group(2))))
    left = _LEFT.search(str(line))
    if left:
        spelled = spell_left(left.group(1))
        if spelled:
            said.append(spelled)
    return " · ".join(said)


def whole(line) -> float:
    found = _WHOLE.search(str(line))
    return _bytes(found.group(1), found.group(2)) if found else 0.0


def _run(url, folder, quality, log, progress, cancel, engine, whole_time):
    started = time.time()
    speaks_utf8 = tool_env(PYTHONUTF8="1")
    process = subprocess.Popen(command(url, folder, quality, engine=engine),
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace",
                               bufsize=1, env=speaks_utf8, **_ff._NOWIN)
    seen = []
    trouble = []
    try:
        for line in process.stdout:
            line = line.rstrip()
            if not line:
                continue
            if cancel and cancel():
                stop(process)
                raise RuntimeError("скачивание остановлено")
            step = share(line)
            if step is not None:
                if progress:
                    progress(step, note(line))
                continue
            done = moment(line)
            if done >= 0:
                if progress and whole_time > 0:
                    progress(min(1.0, done / whole_time),
                             "%s из %s" % (_clock(done), _clock(whole_time)))
                continue
            if line.startswith(("frame=", "size=", "bitrate=", "speed=",
                                "total_size=", "out_time", "progress=",
                                "dup_frames=", "drop_frames=", "fps=",
                                "stream_", "bitrate")):
                continue
            if os.path.isabs(line) and os.path.isfile(line):
                seen.append(line)
                continue
            if line.upper().startswith("ERROR"):
                trouble.append(line)
            if log:
                log(line)
    finally:
        if process.stdout:
            process.stdout.close()
        process.wait()
    return process.returncode, seen, trouble, started


def stop(process) -> None:
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           capture_output=True, timeout=20, **_ff._NOWIN)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.terminate()
    except OSError:
        pass


def _clock(seconds) -> str:
    seconds = int(max(0.0, seconds))
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def flaky(trouble) -> bool:
    text = " ".join(trouble or ()).lower()
    return any(mark in text for mark in FLAKY)


def explain(line) -> str:
    line = str(line or "")
    found = _FF_EXIT.search(line)
    if not found:
        return line
    code = int(found.group(1))
    if code >= 2 ** 31:
        code -= 2 ** 32
    if code == AVERROR_EOF:
        return "ffmpeg: поток оборвался раньше конца видео (сервер закрыл соединение)"
    return "ffmpeg завершился с кодом %d" % code


def rest(seconds, cancel=None) -> None:
    until = time.time() + seconds
    while time.time() < until:
        if cancel and cancel():
            raise RuntimeError("скачивание остановлено")
        time.sleep(0.2)


def _advice(trouble) -> str:
    if not flaky(trouble):
        return ""
    return ("\n\nСвязь с сервером видео всё время обрывается (%d попыток подряд). "
            "Проверьте интернет, VPN или антивирус и нажмите «Скачать» ещё раз."
            % ATTEMPTS)


def _stubborn(url, folder, quality, log, progress, cancel, engine):
    whole_time = 0.0
    started = None
    for tries in range(1, ATTEMPTS + 1):
        if tries > 1:
            if log:
                log("Связь с сервером оборвалась, пробую снова "
                    "(попытка %d из %d): %s" % (tries, ATTEMPTS, explain(trouble[-1])))
            rest(PAUSE, cancel)
        if engine == "ffmpeg" and whole_time <= 0:
            whole_time = length(url)
        code, seen, trouble, began = _run(url, folder, quality, log, progress,
                                          cancel, engine, whole_time)
        started = began if started is None else started
        if code == 0 or seen or not flaky(trouble):
            break
    return code, seen, trouble, started


def download(url, folder, quality=None, log=None, progress=None, cancel=None) -> str:
    if not looks_like_link(url):
        raise ValueError("это не похоже на ссылку: %s" % url)
    if not available():
        raise Missing(
            "yt-dlp не найден.\n"
            "  py -m pip install --upgrade yt-dlp\n"
            "После установки перезапустите Студию.")
    os.makedirs(str(folder), exist_ok=True)
    code, seen, trouble, started = _stubborn(url, folder, quality, log, progress,
                                             cancel, "native")
    if code != 0 and not seen and flaky(trouble):
        raise RuntimeError(explain(trouble[-1]) + _advice(trouble))
    if code != 0 and not seen:
        first = explain(trouble[-1]) if trouble else "yt-dlp вернул %d" % code
        if log:
            log("Обычный загрузчик не справился, перехожу на ffmpeg: %s" % first)
        code, seen, trouble, started = _stubborn(url, folder, quality, log, progress,
                                                 cancel, "ffmpeg")
        if code != 0 and not seen:
            second = explain(trouble[-1]) if trouble else "yt-dlp вернул %d" % code
            raise RuntimeError("Скачать не получилось.\nОбычный загрузчик: %s\n"
                               "Через ffmpeg: %s%s" % (first, second, _advice(trouble)))
    if not seen:
        rescued = newest_since(folder, started)
        if not rescued:
            raise RuntimeError(explain(trouble[-1]) if trouble
                               else "yt-dlp не сказал, куда сохранил файл")
        if log:
            log("путь пришёл неразборчиво, взял свежий файл: %s"
                % os.path.basename(rescued))
        return rescued
    return seen[-1]


def newest_since(folder, moment, least=1 << 20):
    best, stamp = "", moment - 1.0
    try:
        names = os.listdir(str(folder))
    except OSError:
        return ""
    for name in names:
        path = os.path.join(str(folder), name)
        try:
            if not os.path.isfile(path) or os.path.getsize(path) < least:
                continue
            when = os.path.getmtime(path)
        except OSError:
            continue
        if when > stamp:
            best, stamp = path, when
    return best
