from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time

import numpy as np

from . import ffmpeg as _ff
from . import loudness as _loud
from . import webcam as _cam

STEP = 4.0
MIN_LEN = 60.0
FACE_GONE = 20.0
STILL = 1.5
STILL_SPAN = 12.0
TALK_SPAN = 10.0
TALK_SHARE = 0.08
LOUD_MARGIN = 6.0
BRIDGE = 12.0
THUMB = 96
ZOOM_WIDTH = 640
SHRINK_WIDTH = 960
ANCHOR_PROBES = 9
OPEN_MS = 30000
READ_MS = 30000
VERSION = 2                # 2: кадры через ffmpeg — на длинных записях твича OpenCV видел лишь час
FRAME_WIDTH = 960

FACE = "нет лица"
SCREEN = "заставка"
SILENCE = "тишина"
VOICE = "не слышно стримера"
PHRASE_REACH = 90.0
PHRASE_SHARE = 0.5
LEAVE_WORDS = re.compile(
    r"(щас|сейчас|скоро|ща)\s+(вернусь|приду)|отойду|отхожу|отошёл|отошел"
    r"|на\s+(минутку|минуточку|пару\s+минут)|в\s+туалет|пописать|покурить",
    re.IGNORECASE)


def open_capture(path):
    import cv2

    try:
        capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG,
                                   [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_MS,
                                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_MS])
        if capture.isOpened():
            return capture
        capture.release()
    except Exception:
        pass
    return cv2.VideoCapture(str(path))


def thumb(frame):
    frame = np.asarray(frame)
    if frame.ndim == 3:
        frame = frame.mean(axis=2)
    stride = max(1, frame.shape[1] // THUMB)
    return frame[::stride, ::stride].astype(np.float32)


def motion(previous, current):
    if previous is None or previous.shape != current.shape:
        return np.nan
    return float(np.mean(np.abs(current - previous)))


def cut(frame, box):
    if not box:
        return frame
    height, width = frame.shape[:2]
    x, y, w, h = (int(round(v)) for v in box)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return frame
    return frame[y0:y1, x0:x1]


class Cascade:
    def __init__(self):
        import cv2

        self.cv2 = cv2
        self.models = []
        for name in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
            path = os.path.join(cv2.data.haarcascades, name)
            if os.path.isfile(path):
                self.models.append(cv2.CascadeClassifier(path))
        if not self.models:
            raise RuntimeError("нет детектора лиц: не найдены каскады OpenCV")

    def __call__(self, image):
        cv2 = self.cv2
        width = float(max(1, image.shape[1]))
        if width < ZOOM_WIDTH:
            image = cv2.resize(image, None, fx=ZOOM_WIDTH / width, fy=ZOOM_WIDTH / width,
                               interpolation=cv2.INTER_CUBIC)
        elif width > SHRINK_WIDTH:
            image = cv2.resize(image, None, fx=SHRINK_WIDTH / width, fy=SHRINK_WIDTH / width,
                               interpolation=cv2.INTER_AREA)
        gray = cv2.equalizeHist(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
        side = max(24, min(image.shape[:2]) // 24)
        for model in self.models:
            found = model.detectMultiScale(gray, 1.15, 5, minSize=(side, side))
            if len(found):
                return list(found)
            found = model.detectMultiScale(cv2.flip(gray, 1), 1.15, 5, minSize=(side, side))
            if len(found):
                return list(found)
        return []


def frame_signals(frames, box=None, detector=None):
    times, moved, face = [], [], []
    previous = None
    for when, frame in frames:
        small = thumb(frame)
        times.append(float(when))
        moved.append(motion(previous, small))
        previous = small
        if detector is None:
            face.append(-1)
        else:
            face.append(1 if len(detector(cut(frame, box))) else 0)
    return {"time": np.asarray(times, dtype=np.float32),
            "motion": np.asarray(moved, dtype=np.float32),
            "face": np.asarray(face, dtype=np.int8)}


def keyframe_gap(path, reach=60.0) -> float:
    """Как часто в записи ключевые кадры: у твича — раз в 2 с."""
    cmd = [_ff.find("ffprobe"), "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
           "-read_intervals", "%%+%d" % int(reach), "-show_entries", "frame=pts_time",
           "-of", "csv=p=0", str(path)]
    done = subprocess.run(cmd, capture_output=True, text=True, **_ff._NOWIN)
    times = []
    for line in (done.stdout or "").split():
        try:
            times.append(float(line.strip(",")))
        except ValueError:
            pass
    return float(np.median(np.diff(times))) if len(times) > 2 else float("inf")


def open_frames(path, step=STEP, width=FRAME_WIDTH, progress=None):
    """Кадры раз в step секунд через ffmpeg: ([(секунда, кадр BGR)], масштаб к оригиналу).

    Ключевые кадры идут не реже шага — декодируются только они, это быстро.
    Иначе декодируется всё и прореживается фильтром.
    """
    info = _ff.probe(path)
    if not info.width or not info.height:
        raise RuntimeError("не открывается видео: %s" % path)
    scale = min(1.0, width / float(info.width))
    out_w = max(2, int(round(info.width * scale / 2.0)) * 2)
    out_h = max(2, int(round(info.height * scale / 2.0)) * 2)
    keys = keyframe_gap(path) <= step + 1e-6
    chain = "scale=%d:%d,showinfo" % (out_w, out_h) if keys else \
        "fps=1/%g:round=down,scale=%d:%d,showinfo" % (step, out_w, out_h)
    cmd = [_ff.find("ffmpeg"), "-v", "info"] + (["-skip_frame", "nokey"] if keys else []) + [
        "-i", str(path), "-an", "-vf", chain, "-fps_mode", "passthrough",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    total = info.duration

    def frames():
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   **_ff._NOWIN)
        times = []

        def read_times():
            for line in process.stderr:
                found = re.search(rb"pts_time:\s*([\d.]+)", line)
                if found:
                    times.append(float(found.group(1)))

        reader = threading.Thread(target=read_times, daemon=True)
        reader.start()
        size = out_w * out_h * 3
        index, next_at = 0, 0.0
        try:
            while True:
                data = process.stdout.read(size)
                if len(data) < size:
                    break
                waited = 0.0
                while len(times) <= index and waited < 5.0:
                    time.sleep(0.01)
                    waited += 0.01
                when = times[index] if len(times) > index else index * step
                index += 1
                if when + 1e-3 < next_at:
                    continue
                next_at = when + step
                yield when, np.frombuffer(data, np.uint8).reshape(out_h, out_w, 3)
                if progress:
                    progress(when, total)
        finally:
            process.stdout.close()
            process.kill()
            process.wait()
            reader.join(5)

    return frames(), scale


def sample_frames(capture, step=STEP, start=0.0, end=None, progress=None):
    import cv2

    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    total = count / fps if fps > 0 and count > 0 else 0.0
    if end is None or (total and end > total):
        end = total
    when = float(start)
    misses = 0
    while not end or when < end:
        capture.set(cv2.CAP_PROP_POS_MSEC, when * 1000.0)
        ok, frame = capture.read()
        if not ok:
            misses += 1
            if misses >= 3 or not end:
                break
            when += step
            continue
        misses = 0
        yield when, frame
        if progress:
            progress(when, end)
        when += step


def grab_frame(path, at):
    """Один кадр в полном размере, BGR — через ffmpeg с перемоткой по ключевым кадрам."""
    info = _ff.probe(path)
    if not info.width or not info.height:
        return None
    cmd = [_ff.find("ffmpeg"), "-v", "error", "-ss", "%.3f" % at, "-i", str(path),
           "-frames:v", "1", "-an", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    done = subprocess.run(cmd, capture_output=True, **_ff._NOWIN)
    size = info.width * info.height * 3
    if done.returncode != 0 or len(done.stdout) < size:
        return None
    return np.frombuffer(done.stdout[:size], np.uint8).reshape(info.height, info.width, 3)


def locate_box(path, photo, probes=ANCHOR_PROBES, grab=None):
    """Где вебка стримера: пробные кадры по всей длине записи, место — по согласию."""
    locator = _cam.Locator(photo)
    if not locator.usable():
        return None
    total = _ff.duration(path)
    if total <= 0:
        return None
    grab = grab or grab_frame
    found = []
    for index in range(probes):
        frame = grab(path, total * (index + 0.5) / probes)
        if frame is None:
            continue
        hit = locator.find(frame)
        if hit:
            found.append(hit)
    report = _cam.consensus(found)
    if not report or _cam.closeup(report):
        return None
    return report["box"]


def lasting(flags, need):
    flags = np.asarray(flags, dtype=bool)
    out = np.zeros_like(flags)
    need = max(1, int(need))
    index = 0
    while index < flags.size:
        if not flags[index]:
            index += 1
            continue
        stop = index
        while stop < flags.size and flags[stop]:
            stop += 1
        if stop - index >= need:
            out[index:stop] = True
        index = stop
    return out


def talk_share(levels, times, span=TALK_SPAN):
    level = np.asarray(levels["level"], dtype=np.float32)
    voice = np.asarray(levels["voice"], dtype=np.float32)
    window = float(levels["window"])
    times = np.asarray(times, dtype=np.float32)
    if level.size == 0:
        return np.full(times.size, np.nan, dtype=np.float32)
    floor = float(np.percentile(level, 10))
    talk = ((level > floor + LOUD_MARGIN) & (voice >= np.median(voice))).astype(np.float32)
    total = np.concatenate([[0.0], np.cumsum(talk)])
    half = span / 2.0
    left = np.clip(((times - half) / window).astype(int), 0, level.size)
    right = np.clip(((times + half) / window).astype(int), 0, level.size)
    width = np.maximum(right - left, 1)
    share = (total[right] - total[left]) / width
    share[right <= left] = np.nan
    return share.astype(np.float32)


def streamer_share(voice, ref, times, span=TALK_SPAN, same=None):
    from . import voiceid as _voice

    times = np.asarray(times, dtype=np.float32)
    if voice is None or ref is None or not np.asarray(voice["time"]).size:
        return None
    score = np.nan_to_num(_voice.similarity(voice, ref), nan=-1.0)
    speaks = (score >= (_voice.SAME if same is None else same)).astype(np.float32)
    marks = np.asarray(voice["time"], dtype=np.float32)
    total = np.concatenate([[0.0], np.cumsum(speaks)])
    left = np.searchsorted(marks, times - span / 2.0, side="left")
    right = np.searchsorted(marks, times + span / 2.0, side="right")
    share = np.full(times.size, np.nan, dtype=np.float32)
    have = right > left
    share[have] = (total[right[have]] - total[left[have]]) / (right[have] - left[have])
    return share


def leave_phrases(lines):
    found = []
    for start, _end, text in lines or ():
        hit = LEAVE_WORDS.search(text or "")
        if hit:
            found.append((float(start), hit.group(0).strip()))
    return found


def flags(signals, share=None, step=STEP, face_gone=FACE_GONE, still=STILL,
          voice_share=None):
    times = np.asarray(signals["time"], dtype=np.float32)
    moved = np.asarray(signals["motion"], dtype=np.float32)
    face = np.asarray(signals["face"])
    known = moved.copy()
    if known.size and np.isnan(known[0]):
        known[0] = known[1] if known.size > 1 else 0.0
    screen = lasting(known < still, STILL_SPAN / step)
    gone = lasting(face == 0, face_gone / step) if (face >= 0).any() else None
    quiet = None
    mute = None
    if voice_share is not None and not np.all(np.isnan(voice_share)):
        mute = np.nan_to_num(voice_share, nan=1.0) < TALK_SHARE
    elif share is not None and not np.all(np.isnan(share)):
        quiet = np.nan_to_num(share, nan=1.0) < TALK_SHARE
    return {"time": times, FACE: gone, SCREEN: screen, SILENCE: quiet, VOICE: mute}


def segments(marks, step=STEP, min_len=MIN_LEN, bridge=BRIDGE, end=None):
    times = marks["time"]
    if times.size == 0:
        return []
    kinds = [name for name in (FACE, SCREEN, SILENCE, VOICE) if marks.get(name) is not None]
    if not kinds:
        return []
    if FACE in kinds and len(kinds) > 1:
        away = marks[FACE] & np.any([marks[name] for name in kinds if name != FACE], axis=0)
    else:
        votes = np.sum([marks[name].astype(np.int8) for name in kinds], axis=0)
        away = votes >= min(2, len(kinds))
    reach = max(0, int(round(bridge / step)))
    index = 0
    spans = []
    while index < away.size:
        if not away[index]:
            index += 1
            continue
        stop = index
        while True:
            while stop < away.size and away[stop]:
                stop += 1
            ahead = stop
            while ahead < away.size and not away[ahead] and ahead - stop < reach:
                ahead += 1
            if ahead < away.size and away[ahead] and ahead - stop <= reach:
                stop = ahead
                continue
            break
        spans.append((index, stop - 1))
        index = stop
    found = []
    for first, last in spans:
        start = max(0.0, float(times[first]) - step / 2.0)
        finish = float(times[last]) + step / 2.0
        if end:
            finish = min(float(end), finish)
        if finish - start < min_len:
            continue
        piece = slice(first, last + 1)
        held = [name for name in kinds if marks[name][piece].mean() >= 0.5]
        found.append((start, finish, ", ".join(held or kinds)))
    return found


def outside(duration, spans):
    kept = []
    cursor = 0.0
    for start, end, *_ in sorted(spans):
        if start > cursor:
            kept.append((cursor, start))
        cursor = max(cursor, end)
    if duration > cursor:
        kept.append((cursor, float(duration)))
    return kept


def stamp_of(path):
    return "%s|%d|%d" % (os.path.abspath(path), os.path.getsize(path),
                         int(os.path.getmtime(path)))


def cache_path(path, step, box, photo, detector_name):
    try:
        stamp = "%s|%.3f|%s|%s|%s|%d" % (stamp_of(path), step, json.dumps(box),
                                         stamp_of(photo) if photo else "",
                                         detector_name, VERSION)
    except OSError:
        return None
    name = hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]
    folder = os.path.join(tempfile.gettempdir(), "ave_cache")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return None
    return os.path.join(folder, "away_%s.npz" % name)


def detector_name(detector):
    if detector is None:
        return "нет"
    return getattr(detector, "name", type(detector).__name__)


def scan_signals(path, photo=None, box=None, detector=None, step=STEP,
                 progress=None, cache=True):
    if box is not None:
        box = [round(float(v), 1) for v in box]
        photo = None
    if detector is None:
        try:
            detector = Cascade()
        except Exception:
            detector = None
    store = cache_path(path, step, box, photo, detector_name(detector)) if cache else None
    if store and os.path.isfile(store):
        try:
            saved = np.load(store)
            found = saved["box"].tolist() if saved["box"].size else None
            return {"time": saved["time"], "motion": saved["motion"],
                    "face": saved["face"], "box": found}
        except Exception:
            pass
    if box is None and photo:
        box = locate_box(path, photo)
    frames, scale = open_frames(path, step, progress=progress)
    signals = frame_signals(frames, [v * scale for v in box] if box else None, detector)
    if store and signals["time"].size:
        try:
            np.savez_compressed(store, box=np.asarray(box or [], dtype=np.float32),
                                **signals)
        except Exception:
            pass
    signals["box"] = box
    return signals


def find_away(signals, levels=None, step=STEP, min_len=MIN_LEN, face_gone=FACE_GONE,
              bridge=BRIDGE, end=None, voice=None, ref=None, lines=None):
    voice_share = streamer_share(voice, ref, signals["time"])
    share = None
    if voice_share is None and levels is not None:
        share = talk_share(levels, signals["time"])
    marks = flags(signals, share, step=step, face_gone=face_gone, voice_share=voice_share)
    phrases = leave_phrases(lines)
    shortest = min_len * PHRASE_SHARE if phrases else min_len
    found = segments(marks, step=step, min_len=shortest, bridge=bridge, end=end)
    out = []
    for start, finish, why in found:
        said = [text for at, text in phrases if start - PHRASE_REACH <= at <= start + step]
        if finish - start < min_len and not said:
            continue
        if said:
            why = "%s, сказал «%s»" % (why, said[-1])
        out.append((start, finish, why))
    return out


def scan_away(path, photo=None, box=None, detector=None, step=STEP, min_len=MIN_LEN,
              face_gone=FACE_GONE, levels=None, progress=None, cache=True,
              voice=None, ref=None, lines=None, samples=None):
    signals = scan_signals(path, photo=photo, box=box, detector=detector, step=step,
                           progress=progress, cache=cache)
    if levels is None:
        try:
            levels = _loud.audio_levels(path, cache=cache, samples=samples)
        except Exception:
            levels = None
        if levels is not None and not np.asarray(levels["level"]).size:
            levels = None
    end = None
    if levels is not None:
        end = float(np.asarray(levels["level"]).size * levels["window"])
    elif signals["time"].size:
        end = float(signals["time"][-1]) + step / 2.0
    return find_away(signals, levels, step=step, min_len=min_len,
                     face_gone=face_gone, end=end, voice=voice, ref=ref, lines=lines)
