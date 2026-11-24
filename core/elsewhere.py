"""Запись трансляции не с твича: YouTube, Kick, VK, GoodGame — по ссылке из анонса.

Записи площадки потом может не быть вовсе, поэтому пишется видео целиком, сразу
во время эфира. Анонс часто выходит раньше начала — streamlink ждёт, пока
трансляция появится. Оборвалась связь — пишется следующий кусок; куски потом
склеиваются без перекодирования в один .mp4.
"""

from __future__ import annotations

import os
import re
import subprocess
import time

from . import ffmpeg as _ff

QUALITY = "best"
WAIT_START = 1800.0       # столько ждать начала эфира после анонса
GAP = 300.0               # обрыв: столько ждать, что эфир вернётся
RETRY_EVERY = 30.0
LEAST = 1 << 20           # кусок меньше мегабайта — эфира не было


def base_name(platform, channel, url, clock=None) -> str:
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime((clock or time.time)()))
    who = channel or re.sub(r"[^A-Za-z0-9_-]+", "_", url.split("//", 1)[-1])[-40:]
    return "%s_%s_%s" % (platform, who.strip("_") or "live", stamp)


def size(path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def command(url, path, wait, first=True, binary=None) -> list:
    from . import live as _live

    tries = max(1, int(wait // RETRY_EVERY))
    cmd = [binary or _live.streamlink(), "--retry-streams", "%d" % RETRY_EVERY,
           "--retry-max", str(tries), "--hls-live-edge", "3"]
    if first:
        cmd.append("--hls-live-restart")       # опоздали к началу — берём, что держит площадка
    return cmd + [url, QUALITY, "-o", str(path), "-f"]


def run_streamlink(url, path, wait, first, cancel, popen=None):
    process = (popen or subprocess.Popen)(command(url, path, wait, first),
                                          stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, **_ff._NOWIN)
    while process.poll() is None:
        if cancel():
            process.terminate()
            try:
                process.wait(timeout=10)
            except Exception:
                process.kill()
            return
        time.sleep(1.0)


def join(parts, target) -> str:
    """Куски .ts → один .mp4 без перекодирования."""
    source = parts[0] if len(parts) == 1 else "concat:" + "|".join(parts)
    cmd = [_ff.find("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", source,
           "-map", "0", "-c", "copy", "-bsf:a", "aac_adtstoasc", str(target)]
    done = subprocess.run(cmd, capture_output=True, **_ff._NOWIN)
    if done.returncode != 0 or size(target) <= 0:
        return parts[0] if len(parts) == 1 else ""
    for part in parts:
        try:
            os.unlink(part)
        except OSError:
            pass
    return str(target)


def record(url, folder, base, cancel=None, log=None, run=None, glue=None,
           wait=WAIT_START, gap=GAP) -> str:
    """Записать эфир по ссылке до конца. Ответ: путь к .mp4 или "", если эфира не было."""
    cancel = cancel or (lambda: False)
    log = log or (lambda text: None)
    run = run or run_streamlink
    glue = glue or join
    os.makedirs(folder, exist_ok=True)
    parts = []
    while not cancel():
        path = os.path.join(folder, "%s_%d.ts" % (base, len(parts) + 1))
        run(url, path, gap if parts else wait, not parts, cancel)
        if size(path) >= LEAST:
            parts.append(path)
            log("записан кусок %d: %d МБ" % (len(parts), size(path) >> 20))
            continue
        if os.path.isfile(path):
            os.unlink(path)
        break
    if not parts:
        log("эфир так и не начался: %s" % url)
        return ""
    return glue(parts, os.path.join(folder, base + ".mp4"))
