"""Звук, который твич заглушил в записи стрима.

Твич глушит в записи куски по три минуты, где играла чужая музыка. Здесь — где эти
куски (по ответу твича или по тишине) и как положить на их место свой звук, записанный
службой во время эфира.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import urllib.request

import numpy as np

from . import ffmpeg as _ff

GQL = "https://gql.twitch.tv/gql"
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"     # общий ключ сайта твича, не личный
QUERY = ('{ video(id: "%s") { muteInfo { mutedSegmentConnection '
         '{ nodes { duration offset } } } } }')
SILENCE_DB = -80
SILENCE_LEAST = 20.0
RATE = 16000
PROBE = 10.0
QUIET = 1e-4
GAIN_RANGE = (0.3, 3.0)
FADE = 0.03


def vod_id(path) -> str:
    """Номер записи твича из имени файла: «… [v2918345272].mp4»."""
    found = re.search(r"\[v(\d{6,})\]", os.path.basename(str(path)))
    return found.group(1) if found else ""


def original_of(path) -> str:
    """Для копии .seekable — исходная запись рядом, у неё в имени номер записи."""
    from . import cache as _cache

    path = str(path)
    if vod_id(path):
        return path
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = os.listdir(folder)
    except OSError:
        return path
    for name in names:
        other = os.path.join(folder, name)
        if not vod_id(other) or not os.path.isfile(other):
            continue
        twin = str(_cache.remux_beside(other))
        if os.path.isfile(twin) and os.path.samefile(twin, path):
            return other
    return path


def merge(spans) -> list:
    out = []
    for a, b in sorted((float(a), float(b)) for a, b in spans):
        if out and a <= out[-1][1] + 0.5:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def twitch_ranges(vod, ask=None) -> list:
    """Заглушённые куски записи по данным самого твича: [(от, до)] в секундах."""
    def default(body):
        request = urllib.request.Request(GQL, data=json.dumps(body).encode("utf-8"), headers={
            "Client-ID": CLIENT_ID, "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as answer:
            return json.loads(answer.read().decode("utf-8"))

    data = (ask or default)({"query": QUERY % vod})
    video = (data.get("data") or {}).get("video") or {}
    nodes = (((video.get("muteInfo") or {}).get("mutedSegmentConnection") or {})
             .get("nodes") or [])
    return merge((node["offset"], node["offset"] + node["duration"]) for node in nodes)


def silent_ranges(path, noise=SILENCE_DB, least=SILENCE_LEAST) -> list:
    """Запасной путь без номера записи: куски полной тишины длиннее least секунд."""
    cmd = [_ff.binary("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-vn",
           "-af", "silencedetect=noise=%ddB:d=%g" % (noise, least), "-f", "null", "-"]
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **_ff._NOWIN)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", done.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", done.stderr)]
    if len(ends) < len(starts):
        ends.append(_ff.duration(path))
    return merge(zip(starts, ends))


def cache_path(path) -> str:
    return str(path) + ".muted.json"


def muted_ranges(path, ask=None, log=None) -> list:
    """Где в записи стрима заглушён звук. Ответ кешируется рядом с файлом."""
    log = log or (lambda text: None)
    stored = cache_path(path)
    try:
        with open(stored, encoding="utf-8") as handle:
            return [tuple(span) for span in json.load(handle)["muted"]]
    except (OSError, ValueError, KeyError):
        pass
    vod = vod_id(original_of(path))
    spans, source = None, ""
    if vod:
        try:
            spans, source = twitch_ranges(vod, ask=ask), "твич"
        except Exception as error:
            log("твич не ответил про мут: %s — ищу тишину в записи" %
                str(error).splitlines()[0])
    if spans is None:
        spans, source = silent_ranges(path), "тишина"
    try:
        with open(stored, "w", encoding="utf-8") as handle:
            json.dump({"muted": spans, "source": source}, handle)
    except OSError:
        pass
    if spans:
        log("заглушено в записи (%s): %d кусков, %.0f мин" % (
            source, len(spans), sum(b - a for a, b in spans) / 60.0))
    return spans


def read_audio(path, start, length, rate=RATE) -> np.ndarray:
    cmd = [_ff.binary("ffmpeg"), "-hide_banner", "-loglevel", "error",
           "-ss", "%.3f" % max(0.0, start), "-t", "%.3f" % length, "-i", str(path),
           "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    done = subprocess.run(cmd, capture_output=True, **_ff._NOWIN)
    return np.frombuffer(done.stdout, np.int16).astype(np.float32) / 32768.0


def rms(samples) -> float:
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


def placements(keeps, found) -> list:
    """Куски стрима в ролике (keeps: [(начало в стриме, длина)]) → куда класть звук.

    Ответ: [(с какой секунды ролика, сколько, файл, с какой секунды файла, rate, gain)].
    """
    out = []
    offset = 0.0
    for piece_start, piece_len in keeps:
        piece_end = piece_start + piece_len
        for patch in found:
            a, b = max(patch["start"], piece_start), min(patch["end"], piece_end)
            if b - a < 0.2:
                continue
            out.append((offset + a - piece_start, b - a, patch["clip"],
                        patch["from"] + (a - patch["start"]) / patch["rate"], patch["rate"],
                        patch["gain"]))
        offset += piece_len
    return out


def graph(spots):
    """(граф, файлы) для ffmpeg: свой звук на местах мута глушится, сверху — нарезчика."""
    clips = []
    for spot in spots:
        if spot[2] not in clips:
            clips.append(spot[2])
    windows = "+".join("between(t,%.3f,%.3f)" % (at, at + length)
                       for at, length, *_ in spots)
    lines = ["[0:a]aformat=sample_rates=48000,"
             "volume=0:enable='%s'[base]" % windows]
    names = ["[base]"]
    for number, (at, length, clip, source, rate, gain) in enumerate(spots):
        took = length / rate
        chain = ("[%d:a]atrim=start=%.3f:duration=%.3f,asetpts=PTS-STARTPTS,"
                 "aformat=sample_rates=48000"
                 % (clips.index(clip) + 1, source, took))
        if abs(rate - 1.0) > 0.005:
            chain += ",atempo=%.5f" % (1.0 / rate)
        chain += (",volume=%.4f,afade=t=in:d=%g,afade=t=out:st=%.3f:d=%g,"
                  "adelay=%d:all=1[m%d]" % (gain, FADE, max(0.0, length - FADE), FADE,
                                            int(round(at * 1000)), number))
        lines.append(chain)
        names.append("[m%d]" % number)
    lines.append("%samix=inputs=%d:normalize=0:duration=first[aout]" % (
        "".join(names), len(names)))
    return ";\n".join(lines), clips


def apply(video, moment, found, log=None) -> int:
    """Кладёт звук нарезчика в собранный ролик на места мута. Ответ: сколько мест."""
    try:                                   # в Студии clipmaker — модуль, из корня — пакет
        from clipmaker import keep_pieces
    except ImportError:
        from clipmaker.clipmaker import keep_pieces

    log = log or (lambda text: None)
    keeps = keep_pieces(moment["start"], moment["end"] - moment["start"], moment.get("drops"))
    spots = placements(keeps, found)
    if not spots:
        return 0
    text, clips = graph(spots)
    with tempfile.NamedTemporaryFile("w", suffix=".ffgraph", delete=False,
                                     encoding="utf-8") as handle:
        handle.write(text)
        script = handle.name
    base, ext = os.path.splitext(str(video))
    temp = base + ".mute" + ext
    cmd = [_ff.binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(video)]
    for clip in clips:
        cmd += ["-i", clip]
    cmd += ["-/filter_complex", script, "-map", "0:v", "-c:v", "copy", "-map", "[aout]",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", temp]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", **_ff._NOWIN)
    finally:
        os.unlink(script)
    if done.returncode != 0 or not os.path.isfile(temp):
        log("звук нарезчика не вставился: %s" % done.stderr.strip()[-300:])
        if os.path.isfile(temp):
            os.unlink(temp)
        return 0
    os.replace(temp, str(video))
    log("звук на месте мута твича: %d мест, %.0f с" % (
        len(spots), sum(spot[1] for spot in spots)))
    return len(spots)

