# -*- coding: utf-8 -*-
"""Поиск участков, где стример отошёл.

Подставные кадры и уровни звука: двадцать минут стрима, в них отлучка
в туалет (лица нет, тихо, игра идёт), заставка «скоро вернусь» под музыку,
короткая отлучка меньше минуты и молчаливое чтение чата перед статичным
экраном. Отрезками должны стать только первые две, с верной причиной.
Затем то же через настоящий файл: один декодер, кеш, ход.
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

import numpy as np

from core import away

ok = []


def check(label, value, note=""):
    ok.append(bool(value))
    print("  %-54s %s  %s" % (label, "ок" if value else "ПРОВАЛ", note))


STEP = away.STEP
LENGTH = 1200.0
BOX = (8, 8, 24, 20)
TOILET = (300.0, 420.0)
SCREEN = (700.0, 790.0)
SHORT = (900.0, 940.0)
READING = (1000.0, 1100.0)
random = np.random.default_rng(3)


def inside(t, span):
    return span[0] <= t < span[1]


def picture(t, height=72, width=128):
    if inside(t, SCREEN) or inside(t, READING):
        frame = np.full((height, width, 3), 60, dtype=np.uint8)
        frame[30:40, 50:90] = 200
    else:
        frame = random.integers(0, 120, (height, width, 3), dtype=np.uint8)
    if not (inside(t, TOILET) or inside(t, SCREEN) or inside(t, SHORT)):
        x, y, w, h = BOX
        frame[y:y + h, x:x + w] = 230 if not inside(t, READING) else 231
    return frame


class Marker:
    name = "метка"

    def __init__(self):
        self.calls = 0

    def __call__(self, image):
        self.calls += 1
        return [(0, 0, 4, 4)] if float(image.mean()) > 200 else []


def levels(window=0.25):
    count = int(LENGTH / window)
    times = np.arange(count) * window
    level = np.full(count, 10.0, dtype=np.float32)
    voice = np.full(count, 0.3, dtype=np.float32)
    talking = (np.sin(times * 5.0) > -0.3)
    quiet = np.array([inside(t, TOILET) or inside(t, SHORT) or inside(t, READING)
                      for t in times])
    music = np.array([inside(t, SCREEN) for t in times])
    speak = talking & ~quiet & ~music
    level[speak] = 40.0
    voice[speak] = 0.8
    level[music] = 38.0
    voice[music] = 0.9
    level += random.normal(0.0, 0.5, count).astype(np.float32)
    return {"level": level, "voice": voice, "window": window}


def near(found, span, reason_bits):
    for start, end, reason in found:
        if abs(start - span[0]) <= STEP and abs(end - span[1]) <= STEP:
            return all(bit in reason for bit in reason_bits), (start, end, reason)
    return False, None


AUDIO = levels()

print("\n--- подставные кадры и уровни ---")
detector = Marker()
frames = [(t, picture(t)) for t in np.arange(0.0, LENGTH, STEP)]
signals = away.frame_signals(frames, BOX, detector)
check("сигналы на каждый снимок", signals["time"].size == len(frames) == detector.calls)
check("лицо видно вне отлучек", signals["face"][10] == 1 and signals["face"][80] == 0)
found = away.find_away(signals, AUDIO, end=LENGTH)
print("  найдено:", [(round(a), round(b), r) for a, b, r in found])
hit, item = near(found, TOILET, [away.FACE, away.SILENCE])
check("туалет: нет лица и тишина", hit, item)
hit, item = near(found, SCREEN, [away.FACE, away.SCREEN])
check("заставка под музыку: нет лица и неподвижный кадр", hit, item)
check("музыка на заставке не считается тишиной",
      bool(item) and away.SILENCE not in item[2], item)
check("отлучка меньше минуты не попала",
      not any(a < SHORT[1] and b > SHORT[0] for a, b, _ in found))
check("молча читает чат при статичном экране — не отлучка",
      not any(a < READING[1] and b > READING[0] for a, b, _ in found))
check("ровно два отрезка", len(found) == 2)

loose = away.find_away(signals, AUDIO, end=LENGTH, min_len=30.0)
check("порог длины настраивается: 30 с ловит короткую",
      any(abs(a - SHORT[0]) <= STEP for a, _, _ in loose), len(loose))

blind = dict(signals, face=np.full(signals["face"].size, -1, dtype=np.int8))
plain = away.find_away(blind, AUDIO, end=LENGTH)
check("без детектора лиц: заставка+тишина, читающий тоже",
      any(a < READING[1] and b > READING[0] for a, b, _ in plain)
      and not any(a < TOILET[1] and b > TOILET[0] for a, b, _ in plain),
      [(round(a), round(b), r) for a, b, r in plain])

deaf = away.find_away(signals, None, end=LENGTH)
check("без звука: опора на лицо и заставку",
      near(deaf, SCREEN, [away.FACE, away.SCREEN])[0]
      and not any(a < TOILET[1] and b > TOILET[0] for a, b, _ in deaf))

bridged = np.array(signals["face"])
gap = int(350.0 / STEP)
bridged[gap] = 1
joined = away.find_away(dict(signals, face=bridged), AUDIO, end=LENGTH)
check("один ложный кадр с лицом не рвёт отрезок", near(joined, TOILET, [away.FACE])[0])

kept = away.outside(LENGTH, found)
check("outside: остаток без отлучек", len(kept) == 3 and kept[0][0] == 0.0
      and kept[-1][1] == LENGTH and abs(sum(b - a for a, b in kept)
                                        + sum(b - a for a, b, _ in found) - LENGTH) < 1e-6)
check("пустой вход — пустой ответ",
      away.find_away({"time": np.zeros(0), "motion": np.zeros(0),
                      "face": np.zeros(0, np.int8)}, None) == [])

print("\n--- настоящий файл: один декодер, кеш ---")
try:
    import cv2
except ImportError:
    cv2 = None
if cv2 is None:
    print("  нет OpenCV — проверка файла пропущена")
else:
    folder = tempfile.mkdtemp(prefix="away_")
    try:
        path = _os.path.join(folder, "stream.avi")
        fps = 2.0
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), fps, (128, 72))
        for index in range(int(LENGTH * fps)):
            writer.write(picture(index / fps))
        writer.release()
        opened = []
        real_open = away.open_frames

        def counting(source, *args, **kwargs):
            opened.append(source)
            return real_open(source, *args, **kwargs)

        away.open_frames = counting
        seen = []
        store = away.cache_path(path, STEP, list(map(float, BOX)), None, "метка")
        if store and _os.path.isfile(store):
            _os.remove(store)
        started = time.time()
        first = away.scan_away(path, box=BOX, detector=Marker(), levels=AUDIO,
                               progress=lambda done, total: seen.append((done, total)))
        took = time.time() - started
        print("  найдено:", [(round(a), round(b), r) for a, b, r in first],
              "за %.1f с" % took)
        check("из файла — те же два отрезка", len(first) == 2
              and near(first, TOILET, [away.FACE])[0] and near(first, SCREEN, [away.FACE])[0])
        check("декодер открыт один раз", len(opened) == 1, len(opened))
        check("ход виден до конца", bool(seen) and seen[-1][0] >= LENGTH - 2 * STEP,
              seen[-1] if seen else "")
        check("сигналы легли в кеш", bool(store) and _os.path.isfile(store))
        lazy = Marker()
        again = away.scan_away(path, box=BOX, detector=lazy, levels=AUDIO)
        check("второй раз из кеша: декодер не открывали, лица не искали",
              again == first and len(opened) == 1 and lazy.calls == 0)
        fresh = away.scan_away(path, box=BOX, detector=Marker(), levels=AUDIO, cache=False)
        check("без кеша — то же самое", fresh == first and len(opened) == 2)
        away.open_frames = real_open
        if store and _os.path.isfile(store):
            _os.remove(store)
    finally:
        shutil.rmtree(folder, ignore_errors=True)

print("\nитого: %d из %d" % (sum(ok), len(ok)))
_sys.exit(0 if all(ok) else 1)
