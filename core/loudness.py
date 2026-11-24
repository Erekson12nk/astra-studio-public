from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile

import numpy as np

from . import ffmpeg as _ff
from .detect import Moment

WINDOW = 0.25
RATE = 16000
LOW = 200.0
HIGH = 3800.0
SPAN = 120.0
GAP = 25.0
FLOOR = 0.8
VERSION = 1
WEIGHTS = {"lift": 1.0, "world": 2.0, "attack": 1.2, "speech": 1.5,
           "sustain": 1.0, "pulse": 0.25}


def smooth_series(values, span):
    steps = max(1, int(round(span)))
    values = np.asarray(values, dtype=np.float32)
    if steps <= 1 or values.size < 2:
        return values
    kernel = np.ones(steps, dtype=np.float32) / float(steps)
    padded = np.pad(values, steps, mode="edge")
    return np.convolve(padded, kernel, mode="same")[steps:padded.size - steps].astype(np.float32)


def delay_series(values, steps):
    steps = max(1, int(round(steps)))
    out = np.empty_like(values)
    if steps >= values.size:
        out[:] = values[0]
        return out
    out[:steps] = values[0]
    out[steps:] = values[:-steps]
    return out


def cache_path(path, window, sample_rate):
    try:
        stamp = "%s|%d|%d|%.3f|%d|%d" % (os.path.abspath(path), os.path.getsize(path),
                                         int(os.path.getmtime(path)), window,
                                         sample_rate, VERSION)
    except OSError:
        return None
    name = hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]
    folder = os.path.join(tempfile.gettempdir(), "ave_cache")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return None
    return os.path.join(folder, "scan_%s.npz" % name)


def audio_levels(path, window=WINDOW, sample_rate=RATE, progress=None, cache=True,
                 samples=None):
    store = cache_path(path, window, sample_rate) if cache else None
    if store and os.path.isfile(store):
        try:
            saved = np.load(store)
            return {"level": saved["level"], "voice": saved["voice"], "window": window}
        except Exception:
            pass
    size = int(sample_rate * window)
    taper = np.hanning(size).astype(np.float32)
    freqs = np.fft.rfftfreq(size, 1.0 / sample_rate)
    band = (freqs >= LOW) & (freqs <= HIGH)
    total = _ff.duration(path) if progress else 0.0
    if samples and os.path.isfile(samples):
        process = None
        stream = open(samples, "rb")
        spare = None
    else:
        process = subprocess.Popen(
            [_ff.find("ffmpeg"), "-v", "error", "-i", str(path), "-vn", "-ac", "1",
             "-ar", str(sample_rate), "-f", "s16le", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **_ff._NOWIN,
        )
        stream = process.stdout
        spare = (samples + ".part") if samples else None
    portion = size * 2 * 240
    level, voice = [], []
    tail = b""
    done = 0.0
    sink = open(spare, "wb") if spare else None
    while True:
        data = stream.read(portion)
        if not data:
            break
        if sink is not None:
            sink.write(data)
        data = tail + data
        whole = len(data) // (size * 2)
        tail = data[whole * size * 2:]
        if whole == 0:
            continue
        frames = np.frombuffer(data[:whole * size * 2], dtype=np.int16)
        frames = frames.reshape(whole, size).astype(np.float32) * taper
        power = np.abs(np.fft.rfft(frames, axis=1)) ** 2
        inside = power[:, band].sum(axis=1)
        level.append(10.0 * np.log10(inside / float(size * size) + 1e-6))
        voice.append(inside / (power.sum(axis=1) + 1e-6))
        done += whole * window
        if progress:
            progress(done, total)
    stream.close()
    if process is not None:
        process.wait()
    if sink is not None:
        sink.close()
        if process.returncode == 0 and level:
            os.replace(spare, samples)
        else:
            try:
                os.remove(spare)
            except OSError:
                pass
    if not level:
        return {"level": np.zeros(0, np.float32), "voice": np.zeros(0, np.float32),
                "window": window}
    data = {"level": np.concatenate(level).astype(np.float32),
            "voice": np.concatenate(voice).astype(np.float32), "window": window}
    if store:
        try:
            np.savez_compressed(store, level=data["level"], voice=data["voice"])
        except Exception:
            pass
    return data


def rolling_baseline(level, window, span=SPAN, step=8.0):
    hop = max(1, int(round(step / window)))
    half = max(hop, int(round(span * 0.5 / window)))
    knots = np.arange(0, level.size, hop)
    base = np.empty(knots.size, dtype=np.float32)
    dev = np.empty(knots.size, dtype=np.float32)
    for position, center in enumerate(knots):
        chunk = level[max(0, center - half):center + half]
        middle = float(np.median(chunk))
        base[position] = middle
        dev[position] = max(float(np.median(np.abs(chunk - middle))), 0.5)
    grid = np.arange(level.size, dtype=np.float32)
    return (np.interp(grid, knots, base).astype(np.float32),
            np.interp(grid, knots, dev).astype(np.float32))


def moment_scores(data, weights=None, span=SPAN):
    merged = dict(WEIGHTS)
    if weights:
        merged.update(weights)
    weights = merged
    window = data["window"]
    level = data["level"]
    voice = data["voice"]
    base, dev = rolling_baseline(level, window, span=span)
    calm = smooth_series(level, 2.0 / window)
    lift = (calm - base) / dev
    middle_all = float(np.median(level))
    spread_all = max(float(np.median(np.abs(level - middle_all))), 0.5)
    world = np.clip((calm - middle_all) / spread_all, -2.0, 4.0)
    quick = smooth_series(level, 1.5 / window)
    past = delay_series(smooth_series(level, 12.0 / window), 7.0 / window)
    attack = np.maximum((quick - past) / dev, 0.0)
    middle = float(np.median(voice))
    spread = max(float(np.median(np.abs(voice - middle))), 0.01)
    speech = np.clip((voice - middle) / spread, -1.5, 1.5)
    sustain = smooth_series((level > base + 3.0).astype(np.float32), 16.0 / window)
    steady = smooth_series(level, 3.0 / window)
    swing = np.sqrt(np.maximum(smooth_series(level * level, 3.0 / window) - steady * steady, 0.0))
    center = float(np.median(swing))
    width = max(float(np.median(np.abs(swing - center))), 0.2)
    pulse = np.clip((swing - center) / width, -1.5, 1.5)
    score = (weights["lift"] * lift + weights["world"] * world
             + weights["attack"] * attack + weights["speech"] * speech
             + weights["sustain"] * sustain + weights["pulse"] * pulse)
    return {"score": score.astype(np.float32), "lift": lift.astype(np.float32),
            "base": base, "dev": dev}


def quiet_edge(level, base, index, window, reach, forward):
    span = max(1, int(round(reach / window)))
    if forward:
        low = max(0, index - span // 2)
        high = min(level.size, index + span + 1)
    else:
        low = max(0, index - span)
        high = min(level.size, index + span // 2 + 1)
    if high - low < 2:
        return index
    return low + int(np.argmin(level[low:high] - base[low:high]))


def pick_moments(data, count, min_len, max_len, lead=2.0, gap=GAP,
                 floor=FLOOR, weights=None, span=SPAN):
    level = data["level"]
    window = data["window"]
    if level.size < 8:
        return []
    min_len = max(3.0, float(min_len))
    max_len = max(min_len, float(max_len))
    parts = moment_scores(data, weights=weights, span=span)
    score = parts["score"]
    lift = parts["lift"]
    base = parts["base"]
    duration = level.size * window
    separation = max(1, int(round(max(min_len * 0.8, gap) / window)))
    order = np.argsort(score)[::-1]
    chosen = []
    for index in order:
        if score[index] <= floor:
            break
        if any(abs(int(index) - taken) < separation for taken in chosen):
            continue
        chosen.append(int(index))
        if len(chosen) >= count * 2:
            break

    reach = max(1, int(round(max_len / window)) // 2)
    found = []
    for index in chosen:
        gate = max(0.35 * lift[index], 0.4)
        left = index
        while left > 0 and lift[left - 1] >= gate and index - left < reach:
            left -= 1
        right = index
        while right < lift.size - 1 and lift[right + 1] >= gate and right - index < reach:
            right += 1
        least = int(round(min_len / window))
        if right - left < least:
            left = max(0, left - int((least - (right - left)) * 0.75))
            right = min(level.size - 1, left + least)
        left = quiet_edge(level, base, left, window, lead, False)
        right = quiet_edge(level, base, right, window, lead, True)
        start = max(0.0, left * window)
        end = min(duration, right * window)
        if end - start > max_len:
            end = min(duration, start + max_len)
        if end - start < min_len * 0.5:
            continue
        found.append({"start": float(start), "end": float(end),
                      "peak": float(index * window), "score": float(score[index])})

    found.sort(key=lambda item: item["score"], reverse=True)
    kept = []
    for item in found:
        if any(min(item["end"], other["end"]) - max(item["start"], other["start"]) > 0.0
               for other in kept):
            continue
        kept.append(item)
        if len(kept) >= count:
            break
    return kept


def scan_highlights(path, count, min_len, max_len, lead=2.0, window=WINDOW,
                    sample_rate=RATE, progress=None, cache=True, weights=None,
                    samples=None):
    data = audio_levels(path, window=window, sample_rate=sample_rate,
                        progress=progress, cache=cache, samples=samples)
    return pick_moments(data, count, min_len, max_len, lead=lead, weights=weights)


def as_moments(items) -> list:
    return [Moment(start=item["start"], end=item["end"], score=item["score"],
                   peak=item.get("peak", 0.0), engine="loudness")
            for item in items]
