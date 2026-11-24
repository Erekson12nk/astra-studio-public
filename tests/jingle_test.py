# -*- coding: utf-8 -*-
"""Звук доната находится по одному отмеченному образцу.

Синтетика: десять минут шума с «речью» (плавающие гармоники), в неё трижды
вставлен один и тот же звонок с разной громкостью. Образец — первое
вхождение. Надо найти все три с точностью до полсекунды и ничего лишнего.
Отдельно — скорость поиска на спектрограмме длиной в три часа.
"""

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_TESTS = _HERE if _os.path.basename(_HERE) != "snapshot" else _os.path.dirname(_HERE)
_REPO = _os.path.dirname(_TESTS)
_sys.path.insert(0, _TESTS)
_sys.path.insert(0, _REPO)

import shutil
import tempfile
import time
import wave

import numpy as np

from core import ffmpeg as _ff
from core import jingle

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-54s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


RATE = jingle.RATE
random = np.random.default_rng(7)


def bell(seconds=1.6):
    t = np.arange(int(seconds * RATE)) / float(RATE)
    notes = (880.0, 1175.0, 1568.0, 1318.0)
    out = np.zeros_like(t)
    part = t.size // len(notes)
    for index, freq in enumerate(notes):
        piece = slice(index * part, (index + 1) * part)
        local = t[piece] - t[piece][0]
        tone = np.sin(2 * np.pi * freq * local) + 0.4 * np.sin(2 * np.pi * 2 * freq * local)
        out[piece] = tone * np.exp(-local * 4.0)
    return out.astype(np.float32)


def chatter(seconds):
    count = int(seconds * RATE)
    noise = random.normal(0.0, 0.05, count).astype(np.float32)
    t = np.arange(count) / float(RATE)
    pitch = 140.0 + 40.0 * np.sin(2 * np.pi * 0.3 * t) + 20.0 * np.sin(2 * np.pi * 1.7 * t)
    phase = 2 * np.pi * np.cumsum(pitch) / RATE
    voice = sum(np.sin(k * phase) / k for k in range(1, 8))
    gate = (np.sin(2 * np.pi * 0.21 * t) + np.sin(2 * np.pi * 0.53 * t) > 0).astype(np.float32)
    return noise + (0.12 * voice * gate).astype(np.float32)


LENGTH = 600.0
PLACES = (37.3, 251.8, 488.05)
GAINS = (0.5, 0.25, 0.35)

signal = chatter(LENGTH)
sound = bell()
for place, gain in zip(PLACES, GAINS):
    at = int(place * RATE)
    signal[at:at + sound.size] += gain * sound
signal = np.clip(signal, -1.0, 1.0)

print("\n--- поиск по образцу ---")
data = jingle.spectrum_of(signal)
found = jingle.find_repeats(data, PLACES[0], PLACES[0] + sound.size / float(RATE))
starts = [item["start"] for item in found]
check("найдено ровно три", len(found) == 3, ["%.2f" % s for s in starts])
for place in PLACES:
    near = min((abs(s - place) for s in starts), default=99.0)
    check("звонок на %.2f с — точность до 0.5 с" % place, near <= 0.5, "%.3f" % near)
check("оценки в порядке времени", starts == sorted(starts))
check("у находок есть конец и оценка",
      all(item["end"] > item["start"] and 0 < item["score"] <= 1.01 for item in found))

quiet = jingle.find_repeats(data, PLACES[0], PLACES[0] + sound.size / float(RATE),
                            threshold=0.99)
check("строгий порог оставляет только сам образец", len(quiet) == 1,
      ["%.2f" % item["start"] for item in quiet])
limited = jingle.find_repeats(data, PLACES[0], PLACES[0] + sound.size / float(RATE),
                              limit=2)
check("limit ограничивает число находок", len(limited) == 2)
plain = jingle.find_repeats(data, 100.0, 101.6)
check("образец из болтовни не даёт трёх звонков",
      not any(abs(item["start"] - p) < 0.5 for item in plain for p in PLACES[1:]))
try:
    jingle.find_repeats(data, 10.0, 10.1)
    check("слишком короткий образец — понятная ошибка", False)
except ValueError as error:
    check("слишком короткий образец — понятная ошибка", "коротк" in str(error), str(error))

print("\n--- стрим в файле: ffmpeg, кеш ---")
if _ff.have_ffmpeg():
    folder = tempfile.mkdtemp(prefix="jingle_")
    try:
        path = _os.path.join(folder, "stream.wav")
        with wave.open(path, "wb") as sink:
            sink.setnchannels(1)
            sink.setsampwidth(2)
            sink.setframerate(RATE)
            sink.writeframes((signal * 32767).astype(np.int16).tobytes())
        seen = []
        store = jingle.cache_path(path)
        if store and _os.path.isfile(store):
            _os.remove(store)
        first = jingle.scan_jingle(path, PLACES[0], PLACES[0] + 1.6,
                                   progress=lambda done, total: seen.append(done))
        check("из файла — те же три", len(first) == 3
              and all(abs(a["start"] - b) <= 0.5 for a, b in zip(first, PLACES)),
              ["%.2f" % item["start"] for item in first])
        check("ход виден", bool(seen) and seen[-1] >= LENGTH - 1.0, seen[-1:] if seen else "")
        check("спектрограмма легла в кеш", bool(store) and _os.path.isfile(store))
        started = time.time()
        again = jingle.scan_jingle(path, PLACES[0], PLACES[0] + 1.6)
        check("второй раз из кеша и совпадает",
              [a["start"] for a in again] == [a["start"] for a in first],
              "%.2f с" % (time.time() - started))
        if store and _os.path.isfile(store):
            _os.remove(store)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
else:
    print("  нет ffmpeg — проверка файла пропущена")

print("\n--- скорость на трёх часах ---")
started = time.time()
part = jingle.spectrum_of(signal[:int(60 * RATE)])
per_minute = time.time() - started
check("спектрограмма: три часа укладываются в 3 мин", per_minute * 180 < 180,
      "%.2f с на минуту звука → %.0f с на 3 ч" % (per_minute, per_minute * 180))
hours = int(3 * 3600 / data["hop"])
big = np.tile(data["mel"], (hours // data["mel"].shape[0] + 1, 1))[:hours]
started = time.time()
many = jingle.find_repeats({"mel": big, "hop": data["hop"]}, PLACES[0], PLACES[0] + 1.6)
took = time.time() - started
check("поиск по трём часам быстрее минуты", took < 60, "%.1f с, кадров %d" % (took, hours))
check("на трёх часах найдены звонки всех копий", len(many) >= 3 * (hours // data["mel"].shape[0]),
      len(many))

print("\nитого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
